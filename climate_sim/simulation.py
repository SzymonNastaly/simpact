"""Nested orchestration: isolated teams -> six heads -> teams -> simultaneous pledges."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .agents import Backend, DelegationMember, GeminiBackend, OfflineBackend
from .briefs import load_briefs, DEFAULT_BRIEFS
from .evaluator import ClimateEvaluator, MockClimateEvaluator, commitment_digest
from .meetings import MeetingHub, Participant
from .memory import EventStore, write_json
from .models import Brief, ClimateCommitment, ClimateOutcome, DelegationId, MeetingAction, MemberAction, NegotiationOffer, Role, SimulationConfig, TeamMandate
from .scenes import run_scene
from .voting import ranked_choice


class Simulation:
    def __init__(self, config: SimulationConfig, *, output: Path | None = None,
                 briefs_directory: Path = DEFAULT_BRIEFS,
                 evaluator: ClimateEvaluator | None = None,
                 backend_factory: Callable[[str], Backend] | None = None):
        self.config, self.output = config, output
        self.briefs = load_briefs(briefs_directory, config.us_variant)
        if output:
            output.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.store = EventStore(output / "events.jsonl" if output else None)
        self.evaluator = evaluator or MockClimateEvaluator()
        self.teams: dict[str, list[DelegationMember]] = {}
        self.agents: dict[str, DelegationMember] = {}
        self.mandates: dict[str, TeamMandate] = {}
        self.mandate_history: dict[int, dict[str, TeamMandate]] = {}
        self.commitments: dict[int, dict[str, ClimateCommitment]] = {}
        self.outcomes: dict[int, ClimateOutcome] = {}
        self.offers: dict[str, NegotiationOffer] = {}
        self.accepted_by: dict[str, set[str]] = {}
        self.rejected_by: dict[str, set[str]] = {}
        self.current_round = 1
        self.stage = "prepare"
        self._candidates: dict[str, dict[str, ClimateCommitment]] = {}
        self._model_registry = {}
        for region, brief in self.briefs.items():
            team = []
            for index in range(config.team_size):
                # Exactly one head; larger teams alternate extra economy/climate advisers.
                role = Role.HEAD if index == 0 else (Role.ECONOMIC if index % 2 else Role.CLIMATE)
                suffix = "" if index < 3 else f"_{index}"
                name = f"{region}.{role.value}{suffix}"
                model = config.agent_models.get(name, config.default_model)
                if backend_factory:
                    backend = backend_factory(name)
                else:
                    backend = OfflineBackend() if config.backend == "offline" else GeminiBackend(model)
                member = DelegationMember(name, brief, role, config, backend)
                team.append(member)
                self.agents[name] = member
                self._model_registry[name] = backend.label
            self.teams[region] = team
        if set(config.agent_models) - set(self.agents):
            raise ValueError("Per-agent model map contains unknown member IDs")
        self.heads = [members[0] for members in self.teams.values()]
        # One clock for every meeting phase, so meeting IDs and deadlines stay unique across rounds.
        self.hub = MeetingHub([Participant(id=name, delegation=m.brief.delegation.value, role=m.role.value,
                                           model=m.backend.label) for name, m in self.agents.items()],
                              store=self.store)
        self._meeting_start_tick = 0
        self.store.append("configuration", {"config": config.model_dump(mode="json"),
                          "agent_models": self._model_registry,
                          "sources": {k: {"file": b.source_file, "sha256": b.source_sha256, "license": b.license, "attribution": b.attribution} for k, b in self.briefs.items()}},
                          round=0, visibility="public")

    def context(self, member: DelegationMember) -> dict:
        region = member.brief.delegation.value
        events = self.store.view(member.name, region, self.config.memory_length)
        # Keep full trajectories in the audit log; observations use bounded summaries.
        for event in events:
            if event["kind"] == "climate_outcome":
                event["payload"]["global_emissions_trajectory"] = [p for p in event["payload"]["global_emissions_trajectory"] if p["year"] in (2026, 2030, 2050, 2100)]
        latest = max(self.commitments, default=0)
        offers = [o.model_dump(mode="json") for o in self.offers.values()
                  if o.public or o.sender.value == region or region in {d.value for d in o.recipients}]
        result = {"stage": self.stage, "round": self.current_round, "events": events,
                  "public_commitments": {k: c.model_dump(mode="json") for k, c in self.commitments.get(latest, {}).items()},
                  "offers": offers, "decision_rule": self.config.decision_rule}
        # Live team proposals are shared institutional state, not hidden individual
        # preferences. Keep them available even with a one-event episodic memory.
        result["shared_positions"] = {event.actor: event.payload["commitment"]
            for event in self.store.events if event.kind == "internal_speech"
            and event.round == self.current_round and event.owners == [region]}
        result["shared_positions"] = json.loads(json.dumps(result["shared_positions"]))
        if latest:
            result["latest_outcome"] = {"warming_celsius": self.outcomes[latest].warming_celsius,
                                        "is_mock": self.outcomes[latest].is_mock,
                                        "explanation": self.outcomes[latest].explanation,
                                        "regional_impacts": self.outcomes[latest].regional_impacts}
        if region in self.mandates:
            result["mandate"] = self.mandates[region].model_dump(mode="json")
        if region in self._candidates:
            result["candidates"] = {k: c.model_dump(mode="json") for k, c in self._candidates[region].items()}
        if self.stage == "meet":
            # Live protocol state, like shared_positions: only meetings this member sends or is invited to.
            result["meetings"] = self.hub.view(member.name)["meetings"]
            result["directory"] = [{"id": name, "delegation": m.brief.delegation.value, "role": m.role.value}
                                   for name, m in self.agents.items() if name != member.name]
            result.update(tick=self.hub.tick, phase_tick=self.hub.tick - self._meeting_start_tick,
                          acknowledgement_ticks=self.hub.acknowledgement_ticks)
        return result

    def _scene(self, stage: str, participants: list[DelegationMember], *, simultaneous=False,
               turns=1, resolve: Callable[[dict[str, MemberAction]], None]):
        self.stage = stage
        regions = {m.brief.delegation.value for m in participants}
        visibility = "individual" if stage in ("prepare", "meet") else ("delegation" if stage in ("internal", "mandate", "vote") else "public")
        owners = [m.name for m in participants] if visibility == "individual" else (sorted(regions) if visibility == "delegation" else [])
        if stage == "meet":
            owners = ["researcher"]  # who is busy in meetings is itself private
        self.store.append("scene_started", {"stage": stage, "participants": [m.name for m in participants],
                          "engine": "concordia.simultaneous" if simultaneous else "concordia.sequential", "turns": turns},
                          round=self.current_round, visibility=visibility, owners=owners)
        run_scene(stage, participants, simultaneous=simultaneous, turns=turns,
                  context=self.context, resolve=lambda actions: (self._validate_actions(stage, actions), resolve(actions)))

    def _validate_actions(self, stage: str, actions: dict[str, MemberAction]):
        for name, action in actions.items():
            member = self.agents[name]
            member.validate_action(action, member._context)

    def _prepare(self):
        def resolve(actions):
            for name, action in actions.items():
                self.store.append("individual_preparation", action.model_dump(mode="json"), round=1,
                                  visibility="individual", owners=[name], actor=name)
        self._scene("prepare", list(self.agents.values()), simultaneous=True, resolve=resolve)

    def _internal(self, region: str):
        def resolve(actions):
            for name, action in actions.items():
                self.store.append("internal_speech", {"speech": action.speech, "commitment": action.commitment.model_dump(mode="json")},
                                  round=self.current_round, visibility="delegation", owners=[region], actor=name)
        self._scene("internal", self.teams[region], turns=self.config.internal_turns, resolve=resolve)

    def _mandate(self, region: str):
        proposed: list[TeamMandate] = []
        def proposal(actions):
            mandate = next(iter(actions.values())).mandate
            if mandate.delegation.value != region:
                raise ValueError("Foreign mandate")
            proposed.append(mandate)
            self.store.append("mandate_proposal", mandate.model_dump(mode="json"), round=self.current_round,
                              visibility="delegation", owners=[region], actor=self.teams[region][0].name)
        self._scene("mandate", [self.teams[region][0]], simultaneous=True, resolve=proposal)
        mandate = proposed[0]
        candidates = {"head_proposal": mandate.approved_commitment}
        for event in self.store.events:
            if event.kind == "internal_speech" and event.round == self.current_round and event.owners == [region]:
                candidates[f"shared:{event.actor}"] = ClimateCommitment.model_validate(event.payload["commitment"])
        self._candidates[region] = candidates
        ballots: dict[str, MemberAction] = {}
        def vote(actions):
            for name, action in actions.items():
                if len(action.ranked_candidates) != len(candidates) or set(action.ranked_candidates) != set(candidates):
                    raise ValueError("Ballot must rank all candidates exactly once")
                if set(action.approved_candidates) - set(candidates):
                    raise ValueError("Ballot approves unknown candidates")
                ballots[name] = action
                self.store.append("internal_ballot", {"ranked_candidates": action.ranked_candidates, "approved_candidates": action.approved_candidates},
                                  round=self.current_round, visibility="individual", owners=[name], actor=name)
        self._scene("vote", self.teams[region], simultaneous=True, resolve=vote)
        if self.config.decision_rule == "vote":
            winner, trace = ranked_choice({k: v.ranked_candidates for k, v in ballots.items()}, list(candidates))
        else:
            winner, trace = "head_proposal", []
        # Votes, approvals and the selected commitment are computed by the GM, never invented by the leader.
        mandate.approved_commitment = candidates[winner]
        mandate.internal_support = {name: winner in ballot.approved_candidates for name, ballot in ballots.items()}
        self.mandates[region] = mandate
        self.mandate_history.setdefault(self.current_round, {})[region] = mandate.model_copy(deep=True)
        self.store.append("national_mandate", {"mandate": mandate.model_dump(mode="json"),
                          "decision_rule": self.config.decision_rule, "winning_candidate": winner,
                          "election_rounds": trace, "tie_rule": "Eliminate lexically last candidate among equal minimum vote counts"},
                          round=self.current_round, visibility="delegation", owners=[region])
        del self._candidates[region]

    def _meet(self):
        """Member-to-member meetings across roles and delegations, under the meetings.py protocol. Everyone may
        act on the first tick; after that only members with an invitation to answer or an active meeting do."""
        if not self.config.meeting_ticks:
            return
        self.hub.round_number = self.current_round
        self._meeting_start_tick = self.hub.tick
        def resolve(actions):
            for name, action in actions.items():
                for move in action.meeting_actions:
                    try:
                        self._apply_meeting_action(name, move)
                    except (ValueError, PermissionError) as error:
                        # Simultaneous moves can collide (e.g. a close and a message); tell only the mover.
                        self.store.append("meeting_action_rejected", {"tick": self.hub.tick, "action": move.model_dump(mode="json"),
                                          "error": str(error)}, round=self.current_round, visibility="individual",
                                          owners=[name], actor=name)
        participants = list(self.agents.values())
        for _ in range(self.config.meeting_ticks):
            self._scene("meet", participants, simultaneous=True, resolve=resolve)
            self.hub.advance(1)
            busy = {p for m in self.hub.meetings.values() if m.status == "active" for p in m.participants}
            busy |= {p for m in self.hub.meetings.values() if m.status == "pending"
                     for p, state in m.acknowledgements.items() if state == "pending"}
            participants = [m for name, m in self.agents.items() if name in busy]
            if not participants:
                break

    def _apply_meeting_action(self, actor: str, move: MeetingAction):
        if move.operation == "request":
            self.hub.request(actor, move.invitees, move.topic)
        elif move.operation == "respond":
            self.hub.respond(actor, move.meeting_id, bool(move.accepted))
        elif move.operation == "message":
            self.hub.message(actor, move.meeting_id, move.text)
        else:
            self.hub.close(actor, move.meeting_id)

    def _offer_event(self, offer: NegotiationOffer):
        self.store.append("negotiation_offer", {"offer": offer.model_dump(mode="json"),
                          "accepted_by": sorted(self.accepted_by[offer.id]), "rejected_by": sorted(self.rejected_by[offer.id])},
                          round=self.current_round, visibility="public" if offer.public else "delegation",
                          owners=[] if offer.public else sorted({offer.sender.value, *(d.value for d in offer.recipients)}))

    def _negotiate(self):
        def resolve(actions):
            for name, action in actions.items():
                region = self.agents[name].brief.delegation.value
                self.store.append("summit_speech", {"speech": action.speech}, round=self.current_round,
                                  visibility="public", actor=name)
                for offer_id, response in action.offer_responses.items():
                    offer = self.offers.get(offer_id)
                    if offer is None or region not in {d.value for d in offer.recipients}:
                        raise ValueError("Can respond only to an offer addressed to one's own team")
                    if offer.expires_after_round < self.current_round or offer.status not in ("proposed", "accepted"):
                        raise ValueError("Cannot respond to an expired or terminal offer")
                    if response == "accept":
                        self.accepted_by[offer_id].add(region)
                        self.rejected_by[offer_id].discard(region)
                    else:
                        self.rejected_by[offer_id].add(region)
                        self.accepted_by[offer_id].discard(region)
                    offer.status = "rejected" if self.rejected_by[offer_id] else ("accepted" if set(d.value for d in offer.recipients) <= self.accepted_by[offer_id] else "proposed")
                    self._offer_event(offer)
                for offered in action.offers:
                    if offered.sender.value != region or offered.status != "proposed" or offered.expires_after_round < self.current_round:
                        raise ValueError("Invalid offer sender, initial status or expiration")
                    if self.config.offer_visibility != "mixed" and offered.public != (self.config.offer_visibility == "public"):
                        raise ValueError("Offer violates the configured visibility treatment")
                    base = self.mandates[region].approved_commitment
                    self.briefs[region].check_commitment(base.changed(offered.offered_changes))
                    offer = offered.model_copy(update={"id": f"offer-{len(self.offers) + 1:04d}"}, deep=True)
                    self.offers[offer.id] = offer
                    self.accepted_by[offer.id], self.rejected_by[offer.id] = set(), set()
                    self._offer_event(offer)
        self._scene("negotiate", self.heads, turns=self.config.negotiation_turns, resolve=resolve)

    def _submit(self, stage: str):
        def resolve(actions):
            commitments = {self.agents[name].brief.delegation.value: action.commitment for name, action in actions.items()}
            # All six decisions were collected against frozen pre-submission views.
            if set(commitments) != set(self.briefs):
                raise ValueError("Submission barrier requires all six delegations")
            self.commitments[self.current_round] = commitments
            for name, action in actions.items():
                self.store.append("summit_speech", {"speech": action.speech}, round=self.current_round,
                                  visibility="public", actor=name)
            self.store.append("commitments", {"commitments": {k: c.model_dump(mode="json") for k, c in commitments.items()},
                              "commitment_sha256": commitment_digest(commitments), "simultaneous": True},
                              round=self.current_round, visibility="public")
        self._scene(stage, self.heads, simultaneous=True, resolve=resolve)
        commitments = self.commitments[self.current_round]
        if self.output:
            # Also survives an unavailable reviewed outcome, enabling manual evaluation.
            write_json(self.output / f"round-{self.current_round:02d}-commitments.json", {
                "round": self.current_round,
                "commitments": {k: c.model_dump(mode="json") for k, c in commitments.items()},
                "commitment_sha256": commitment_digest(commitments)})
        outcome = self.evaluator.evaluate(commitments)
        self.outcomes[self.current_round] = outcome
        self.store.append("climate_outcome", outcome.model_dump(mode="json"), round=self.current_round, visibility="public")

    def _settle_offers(self):
        final = self.commitments[self.current_round]
        for offer in self.offers.values():
            if offer.status == "rejected":
                continue
            if offer.expires_after_round < self.current_round:
                offer.status = "expired"
            elif offer.status == "accepted":
                sender = final[offer.sender.value]
                performed = all(getattr(sender, k) <= v if k.endswith("year") else getattr(sender, k) >= v
                                for k, v in offer.offered_changes.items())
                offer.status = "fulfilled" if performed and offer.conditions_met(final) else "unfulfilled"
            self._offer_event(offer)

    def run(self) -> dict:
        from .metrics import calculate_metrics, render_report
        try:
            self._prepare()
            for region in self.teams:
                self._internal(region)
                self._mandate(region)
            self._submit("announce")
            for round_number in range(2, self.config.rounds + 1):
                # International offers inform the next private team deliberation.
                self._negotiate()
                self.current_round = round_number
                for offer in self.offers.values():
                    if offer.expires_after_round < round_number and offer.status in ("proposed", "accepted"):
                        offer.status = "expired"
                        self._offer_event(offer)
                # Team meetings and negotiations (World Climate's round 2+): members reach out before deliberating.
                self._meet()
                for region in self.teams:
                    self._internal(region)
                    self._mandate(region)
                self._negotiate()
                self._submit("submit")
                if self.config.stop_on_target and self.outcomes[round_number].target_2c_achieved:
                    break
            self._settle_offers()
            metrics = calculate_metrics(self)
            report = render_report(self, metrics)
            state = {"status": "complete", "config": self.config.model_dump(mode="json"),
                     "agent_models": self._model_registry,
                     "agents": {k: agent.save_state() for k, agent in self.agents.items()},
                     "mandates": {str(r): {k: m.model_dump(mode="json") for k, m in mandates.items()} for r, mandates in self.mandate_history.items()},
                     "commitments_by_round": {str(r): {k: c.model_dump(mode="json") for k, c in cs.items()} for r, cs in self.commitments.items()},
                     "outcomes_by_round": {str(r): out.model_dump(mode="json") for r, out in self.outcomes.items()},
                     "offers": [{**o.model_dump(mode="json"), "accepted_by": sorted(self.accepted_by[o.id]), "rejected_by": sorted(self.rejected_by[o.id])} for o in self.offers.values()],
                     "meetings": self.hub.view(researcher=True),
                     "metrics": metrics}
            if self.output:
                write_json(self.output / "state.json", state)
                write_json(self.output / "public.json", {"events": self.store.public_view(), "outcomes_by_round": state["outcomes_by_round"]})
                write_json(self.output / "metrics.json", metrics)
                write_json(self.output / "c_roads_inputs.json", [{"round": r, "commitments": state["commitments_by_round"][str(r)], "commitment_sha256": commitment_digest(cs)} for r, cs in self.commitments.items()])
                with (self.output / "report.md").open("x") as stream:
                    stream.write(report)
            return {"state": state, "report": report}
        except Exception as error:
            # Audit partial runs, but never dump provider credentials or arbitrary exception payloads.
            self.store.append("run_failed", {"error_type": type(error).__name__, "stage": self.stage},
                              round=self.current_round, visibility="individual", owners=["researcher"])
            if self.output:
                write_json(self.output / "failure.json", {"status": "failed", "stage": self.stage,
                           "round": self.current_round, "error_type": type(error).__name__, "config": self.config.model_dump(mode="json")})
            raise
