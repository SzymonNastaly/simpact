"""Continuous Round 1 conversations, then bilaterals. One worker, one fresh context per action.

The worker never holds the viewer lock during network calls. A pause allows the
in-flight action to finish, then prevents further calls. No calls on page load.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from typing import Callable

from .agents import Backend, GeminiBackend, OfflineBackend
from .bilaterals import Bilaterals
from .lab import Lab
from .memory import Event
from .models import ClimateCommitment, SimulationConfig
from .scenes import run_scene
from .simulation import Simulation


class LiveLab(Lab):
    def __init__(self, config: SimulationConfig, *, output: Path | None = None,
                 thinking_level: str = "medium", message_budget: int = 12,
                 backend_factory: Callable[[str], Backend] | None = None,
                 resume: Path | None = None):
        saved = None
        if resume:
            if output:
                raise ValueError("Choose a new output directory or a saved live session to resume")
            saved = json.loads((resume / "state.json").read_text())
            if "live" not in saved:
                raise ValueError("Only a live Round 1 session can be resumed")
            # Sessions saved before bilaterals carry settings that no longer exist.
            config = SimulationConfig.model_validate({k: v for k, v in saved["config"].items() if k in SimulationConfig.model_fields})
            thinking_level = saved["live"].get("thinking_level", thinking_level)
            message_budget = saved["live"].get("message_budget", message_budget)
        if not 1 <= message_budget <= 100:
            raise ValueError("Message budget must be between 1 and 100")
        if thinking_level not in {"low", "medium", "high"}:
            raise ValueError("Unknown thinking level")
        if backend_factory is None:
            backend_factory = lambda name: (OfflineBackend() if config.backend == "offline" else
                GeminiBackend(config.agent_models.get(name, config.default_model),
                              thinking_level=thinking_level, include_thoughts=True, max_output_tokens=8192))
        self.sim = Simulation(config, output=output, backend_factory=backend_factory)
        self.events, self.briefs = self.sim.store.events, self.sim.briefs
        self.observer_briefs = self.sim.observer_briefs
        self.output = resume or output
        self.run_name = self.output.name if self.output else "live-round-one"
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.message_budget, self.thinking_level = message_budget, thinking_level
        self.revision = 0
        self.controls = {region: {"active": False, "status": "idle", "thinking_agent": None,
                                 "stage": None, "phase": "discussion", "messages": 0,
                                 "remaining": 0, "error": None, "finish_requested": False,
                                 "finish_resume": False}
                         for region in self.sim.teams}
        self.prepared: set[str] = set()
        self.summarized = {region: set() for region in self.sim.teams}
        self.cursor = {region: 0 for region in self.sim.teams}
        self._region_cursor = 0
        self._closed = False
        # Bilaterals open once every delegation has concluded Round 1; they belong to Round 2.
        self.bilateral = {"active": False, "status": "idle", "thinking_agents": [], "error": None}
        if config.bilateral_slots:
            self.sim.bilaterals = Bilaterals(self.sim.hub, self.sim.store, round_number=2,
                slots=config.bilateral_slots, message_limit=config.bilateral_messages)
        if saved:
            self.events[:] = [Event.model_validate_json(line) for line in (resume / "events.jsonl").read_text().splitlines()]
            self.sim.store.path = resume / "events.jsonl"
            self.sim.output = resume
            self.prepared = {e.actor for e in self.events if e.kind == "individual_preparation"}
            for name, member in self.sim.agents.items():
                state = saved["agents"][name]
                for field, key in (("preferred", "final_preference"), ("initial_preference", "initial_preference")):
                    setattr(member, field, ClimateCommitment.model_validate(state[key]) if state.get(key) else None)
                member.private_strategy = state["private_strategy"]
                member.observed_event_ids = list(state.get("observed_event_ids", []))
                member.action_traces = [json.loads(json.dumps(e.payload)) for e in self.events
                                        if e.kind == "agent_trace" and e.actor == name]
            for region, control in self.controls.items():
                control.update(saved["live"]["controls"].get(region, {}))
                team_events = [e for e in self.events if e.round == 1 and e.owners == [region]]
                self.summarized[region] = {e.actor for e in team_events if e.kind == "round_summary"}
                self.cursor[region] = sum(e.kind == "internal_speech" and not e.payload.get("requested_finish", False) for e in team_events)
                control["messages"] = sum(e.kind == "internal_speech" for e in team_events)
                if any(e.kind == "next_round_plan" for e in team_events):
                    control["phase"] = "completed"
                elif any(e.kind == "discussion_ended" for e in team_events):
                    control["phase"] = "plan" if len(self.summarized[region]) == len(self.sim.teams[region]) else "summaries"
                control.update(active=False, thinking_agent=None, stage=None)
                control["status"] = "completed" if control["phase"] == "completed" else "paused" if team_events else "idle"
            self.sim.hub.load(self.events)
            if self.sim.bilaterals and any(e.kind == "bilateral_nominations" for e in self.events):
                self.bilateral["status"] = "completed" if self.sim.bilaterals.done else "paused"
            self.revision = saved["live"].get("revision", 0) + 1
        self._checkpoint()
        self.worker = threading.Thread(target=self._work, name="round-one-agent-loop", daemon=True)
        self.worker.start()

    def _checkpoint(self):
        self.state = {"config": self.sim.config.model_dump(mode="json"),
                      "agents": {name: member.save_state() for name, member in self.sim.agents.items()},
                      "outcomes_by_round": {}, "commitments_by_round": {},
                      "live": {"round": 1, "controls": self.controls, "bilaterals": self.bilateral, "revision": self.revision,
                               "thinking_level": self.thinking_level, "message_budget": self.message_budget}}
        if self.output:
            temporary = self.output / "state.json.tmp"
            with temporary.open("w", encoding="utf-8") as stream:
                temporary.chmod(0o600)
                json.dump(self.state, stream, ensure_ascii=False, allow_nan=False)
            temporary.replace(self.output / "state.json")

    def round_one(self, scope="public"):
        with self.lock:
            result = super().round_one(scope)
            result.update(mode="live", revision=self.revision, message_budget=self.message_budget)
            member = self.state["agents"].get(scope)
            for country in result["countries"]:
                if scope == "researcher" or (member and member["delegation"] == country["id"]):
                    country["live"] = dict(self.controls[country["id"]])
                    country["live"]["can_finish"] = self._can_finish(country["id"])
            return result

    def bilaterals(self, scope="public"):
        with self.lock:
            result = super().bilaterals(scope)
            result.update(mode="live", revision=self.revision)
            meetings = self.sim.bilaterals
            if scope == "researcher":
                result["live"] = {**self.bilateral, "enabled": meetings is not None, "open": self._bilaterals_open(),
                    "concluded": sum(c["phase"] == "completed" for c in self.controls.values()), "countries": len(self.controls),
                    "slot": min(meetings.slot, meetings.slots) if meetings else 0, "slots": self.sim.config.bilateral_slots,
                    "message_limit": self.sim.config.bilateral_messages, "done": bool(meetings and meetings.done)}
            return result

    def _bilaterals_open(self):
        return all(c["phase"] == "completed" for c in self.controls.values())

    def _act_bilaterals(self, operation):
        control, meetings = self.bilateral, self.sim.bilaterals
        if meetings is None:
            raise ValueError("Bilaterals are turned off for this session")
        if operation == "start":
            if not self._bilaterals_open():
                raise ValueError("Bilaterals open once every delegation has concluded Round 1")
            if meetings.done:
                raise ValueError("Every bilateral slot is complete")
            if not control["active"]:
                control.update(active=True, error=None, status="thinking" if control["thinking_agents"] else "queued")
        elif operation == "pause":
            control.update(active=False, status="pausing" if control["thinking_agents"] else "paused")
        else:
            raise ValueError("Use start or pause for bilaterals")
        self.revision += 1
        self._checkpoint()
        self.condition.notify_all()
        return dict(control)

    def _bilateral_calls(self):
        """Contexts for everyone due in this bilateral step; computed under the lock, before any network call."""
        meetings = self.sim.bilaterals
        due = meetings.due()
        if not due:
            self.bilateral.update(active=False, status="completed")
            return None
        self.sim.current_round = 2
        calls = []
        for stage, name in due:
            member = self.sim.agents[name]
            self.sim.stage = stage
            context = self.sim.context(member)
            context["events"] = self.sim.store.view(name, member.brief.delegation.value, len(self.events) + 1,
                                                  exclude_kinds={"agent_trace", "agent_call_failed"})
            calls.append((stage, member, context))
        self.bilateral.update(status="thinking", thinking_agents=[name for _, name in due])
        self.revision += 1
        self._checkpoint()
        return calls

    def _run_bilaterals(self, calls):
        """One step: every due call runs in parallel and resolves on its own, so one failure loses only its call."""
        slot, failed = self.sim.bilaterals.slot, []
        produced = {"nominate": "bilateral_nominations", "bilateral": "meeting_message", "debrief": "team_meeting_report"}

        def call(stage, member, context):
            def resolve(actions):
                with self.condition:
                    start = len(self.events)
                    self.sim.bilaterals.resolve(stage, actions)
                    trace = member.action_traces[-1]
                    trace["resolution_event_ids"] = [e.id for e in self.events[start:]
                                                     if e.actor == member.name and e.kind == produced[stage]]
                    self.sim.store.append("agent_trace", trace, round=2, visibility="individual",
                                          owners=[member.name], actor=member.name)
                    self.revision += 1
                    self._checkpoint()
            try:
                run_scene(stage, [member], simultaneous=False, turns=1, context=lambda _: context, resolve=resolve)
            except Exception as error:
                with self.condition:
                    failed.append(member.name)
                    self.sim.store.append("agent_call_failed", {"stage": stage, "error_type": type(error).__name__,
                        "generation": getattr(member.backend, "last_generation", None)},
                        round=2, visibility="individual", owners=[member.name], actor=member.name)

        with ThreadPoolExecutor(max_workers=min(16, len(calls))) as pool:
            list(pool.map(lambda item: call(*item), calls))
        with self.condition:
            control, meetings = self.bilateral, self.sim.bilaterals
            control["thinking_agents"] = []
            if failed:
                control.update(active=False, status="error",
                               error=f"{len(failed)} agent call(s) failed. Continue to retry them.")
            elif meetings.done:
                control.update(active=False, status="completed")
            elif meetings.slot != slot:
                # Stop between slots so the conversations can be read before the next round of requests.
                control.update(active=False, status="slot_done")
            else:
                control["status"] = "queued" if control["active"] else "paused"
            self.revision += 1
            self._checkpoint()
            self.condition.notify_all()

    def _can_finish(self, region):
        speakers = {e.actor for e in self.events if e.kind == "internal_speech"
                    and e.round == 1 and e.owners == [region]}
        return self.controls[region]["phase"] == "discussion" and all(
            member.name in speakers for member in self.sim.teams[region])

    def act(self, action):
        with self.condition:
            if self._closed:
                raise ValueError("Live session is closed")
            if action.get("track") == "bilaterals":
                return self._act_bilaterals(action.get("operation"))
            region = action.get("country")
            if region not in self.controls:
                raise ValueError("Unknown country")
            control = self.controls[region]
            operation = action.get("operation")
            if control["phase"] == "completed":
                raise ValueError("This meeting has concluded; its summaries and action plan are final")
            if operation == "start":
                if not control["active"]:
                    control.update(active=True, remaining=self.message_budget, error=None)
                    control["status"] = "thinking" if control["thinking_agent"] else "queued"
            elif operation == "pause":
                control.update(active=False, status="pausing" if control["thinking_agent"] else "paused")
            elif operation == "finish":
                if not self._can_finish(region):
                    raise ValueError("Every member must contribute before asking the head to conclude")
                if not control["finish_requested"]:
                    control.update(finish_requested=True, finish_resume=control["active"], active=True,
                                   remaining=max(1, control["remaining"]), error=None)
                    if not control["thinking_agent"]:
                        control["status"] = "queued"
            else:
                raise ValueError("Use start, pause or finish for live conversations")
            self.revision += 1
            self._checkpoint()
            self.condition.notify_all()
            return {**control, "can_finish": self._can_finish(region)}

    def _next(self):
        regions = list(self.controls)
        for offset in range(len(regions)):
            index = (self._region_cursor + offset) % len(regions)
            region = regions[index]
            control = self.controls[region]
            if control["active"]:
                self._region_cursor = (index + 1) % len(regions)
                team = self.sim.teams[region]
                if control["phase"] == "summaries":
                    member = next(m for m in team if m.name not in self.summarized[region])
                    stage = "summary"
                elif control["phase"] == "plan":
                    member, stage = team[0], "plan"
                else:
                    unprepared = next((m for m in team if m.name not in self.prepared), None)
                    member = unprepared or (team[0] if control["finish_requested"] else team[self.cursor[region] % len(team)])
                    stage = "prepare" if unprepared else "internal"
                control.update(status="thinking", thinking_agent=member.name, stage=stage)
                self.revision += 1
                return region, member, stage
        return None

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self._closed or self.bilateral["active"]
                                        or any(c["active"] for c in self.controls.values()))
                if self._closed:
                    return
                calls = self._bilateral_calls() if self.bilateral["active"] else None
            if calls is not None:
                self._run_bilaterals(calls)
                continue
            with self.condition:
                if (step := self._next()) is None:
                    continue
                region, member, stage = step
                self.sim.stage = stage
                context = self.sim.context(member)
                # Full scoped history: never truncate away messages the agent is responding to.
                context["events"] = self.sim.store.view(member.name, region, len(self.events) + 1,
                                                      exclude_kinds={"agent_trace", "agent_call_failed"})
                context.update(continuous_discussion=True,
                               discussion=[e for e in context["events"] if e["kind"] == "internal_speech"],
                               discussion_revision=self.controls[region]["messages"],
                               team_members=[{"id": m.name, "role": m.role.value} for m in self.sim.teams[region]])
                requested_finish = stage == "internal" and member == self.sim.teams[region][0] and self.controls[region]["finish_requested"]
                context.update(can_finish_discussion=self._can_finish(region), finish_requested=requested_finish,
                               agent_summaries=[e for e in context["events"] if e["kind"] == "round_summary"])

            def resolve(actions):
                with self.condition:
                    action = actions[member.name]
                    if stage == "internal" and not action.speech.strip():
                        raise ValueError("Empty discussion message")
                    if stage == "prepare":
                        payload = action.model_dump(mode="json")
                        kind = "individual_preparation"
                    elif stage == "internal":
                        payload = {"speech": action.speech, "commitment": action.commitment.model_dump(mode="json"),
                                   "requested_finish": requested_finish}
                        kind = "internal_speech"
                    elif stage == "summary":
                        payload = {"summary": action.round_summary.model_dump(mode="json")}
                        kind = "round_summary"
                    else:
                        payload = {"assignments": [task.model_dump(mode="json") for task in action.action_plan], "next_round": 2}
                        kind = "next_round_plan"
                    event = self.sim.store.append(kind,
                        payload, round=1, visibility="individual" if stage == "prepare" else "delegation",
                        owners=[member.name] if stage == "prepare" else [region], actor=member.name)
                    trace = member.action_traces[-1]
                    trace["resolution_event_ids"] = [event.id]
                    self.sim.store.append("agent_trace", trace, round=1, visibility="individual",
                                          owners=[member.name], actor=member.name)
                    if stage == "prepare":
                        self.prepared.add(member.name)
                    elif stage == "summary":
                        self.summarized[region].add(member.name)
                        if len(self.summarized[region]) == len(self.sim.teams[region]):
                            self.controls[region]["phase"] = "plan"
                    elif stage == "plan":
                        self.controls[region].update(active=False, phase="completed", status="completed")
                        self.sim.store.append("discussion_closed", {"summaries": len(self.summarized[region]), "next_round": 2},
                            round=1, visibility="delegation", owners=[region], actor=member.name)
                    else:
                        if not requested_finish:
                            self.cursor[region] += 1
                        self.controls[region]["messages"] += 1
                        self.controls[region]["remaining"] -= 1
                        if action.finish_discussion:
                            self.controls[region].update(phase="summaries", finish_requested=False, finish_resume=False)
                            self.sim.store.append("discussion_ended", {"reason": action.finish_reason},
                                round=1, visibility="delegation", owners=[region], actor=member.name)
                        elif self.controls[region]["remaining"] <= 0:
                            self.controls[region].update(active=False, status="limited")
                        elif requested_finish and not self.controls[region]["finish_resume"]:
                            self.controls[region].update(active=False, status="paused")
                        if requested_finish:
                            self.controls[region].update(finish_requested=False, finish_resume=False)
                    self.revision += 1
                    self._checkpoint()

            try:
                # Retain Concordia's actual observation/action/validation/resolution path.
                run_scene(stage, [member], simultaneous=False, turns=1, context=lambda _: context, resolve=resolve)
            except Exception as error:
                with self.condition:
                    self.controls[region].update(active=False, status="error",
                        error="Agent call failed. Continue to retry the current step.")
                    self.sim.store.append("agent_call_failed", {"stage": stage, "error_type": type(error).__name__,
                        "generation": getattr(member.backend, "last_generation", None)},
                        round=1, visibility="individual", owners=[member.name], actor=member.name)
            finally:
                with self.condition:
                    control = self.controls[region]
                    control.update(thinking_agent=None, stage=None)
                    if control["active"]:
                        control["status"] = "queued"
                    elif control["status"] not in {"error", "limited", "completed"}:
                        control["status"] = "paused"
                    self.revision += 1
                    self._checkpoint()
                    self.condition.notify_all()

    def close(self):
        with self.condition:
            self._closed = True
            for control in self.controls.values():
                control["active"] = False
            self.bilateral["active"] = False
            self.condition.notify_all()
        # A bounded join; an in-flight provider request may still be finishing.
        self.worker.join(timeout=1)
