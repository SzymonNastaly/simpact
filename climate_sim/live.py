"""Live Round 1 conversations, then bilaterals, each run by a Concordia game master.

Every country's Round 1 is one long Concordia loop: a ScheduledGameMaster follows that country's
RoundOneDiscussion (preparation, discussion, the head's closure, summaries, the action plan) and the
engine runs it, one fresh context per action. Live bilaterals use a continuous allocator once every country has
concluded. The viewer's Start and Pause drive each loop's StepController, and budgets and closures pause
a loop between steps, so a pause lets the in-flight call finish. Game-master calls take the viewer lock;
agent calls never hold it. No calls on page load.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from typing import Callable

from concordia.environment.step_controller import StepController

from .allocator import AgentAllocator
from .agents import Backend, DelegationMember, GeminiBackend, OfflineBackend
from .bilaterals import Bilaterals, RELATIONS_STAGE, SUMMARY_STAGE
from .lab import Lab
from .memory import Event
from .models import ClimateCommitment, MemberAction, SimulationConfig
from .scenes import ScheduledGameMaster, run_schedule, run_scene
from .simulation import Simulation


class RoundOneDiscussion:
    """Who acts next in one country's Round 1, and what each contribution causes.

    Phases (control["phase"]): every member prepares, then the team talks in turn until the head
    concludes; every member then summarizes, and the head writes the next-round action plan.
    """
    round = 1

    def __init__(self, sim: Simulation, region: str, control: dict, can_finish: Callable[[], bool]):
        self.sim, self.region, self.control, self.can_finish = sim, region, control, can_finish
        self.team = sim.teams[region]
        names = {m.name for m in self.team}
        team_events = [e for e in sim.store.events if e.round == 1 and e.owners == [region]]
        # Rebuilt from the record, so a resumed session continues where it stopped.
        self.prepared = {e.actor for e in sim.store.events if e.kind == "individual_preparation" and e.actor in names}
        self.summarized = {e.actor for e in team_events if e.kind == "round_summary"}
        self.cursor = sum(e.kind == "internal_speech" and not e.payload.get("requested_finish", False) for e in team_events)

    def due(self) -> list[tuple[str, str]]:
        phase, team = self.control["phase"], self.team
        if phase == "completed":
            return []
        if phase == "summaries":
            return [("summary", next(m for m in team if m.name not in self.summarized).name)]
        if phase == "plan":
            return [("plan", team[0].name)]
        unprepared = next((m for m in team if m.name not in self.prepared), None)
        if unprepared:
            return [("prepare", unprepared.name)]
        member = team[0] if self.control["finish_requested"] else team[self.cursor % len(team)]
        return [("internal", member.name)]

    def _requested_finish(self, member: DelegationMember, stage: str) -> bool:
        return stage == "internal" and member == self.team[0] and self.control["finish_requested"]

    def context(self, member: DelegationMember, stage: str) -> dict:
        context = self.sim.context(member, stage)
        # Full scoped history: never truncate away messages the agent is responding to.
        context["events"] = self.sim.store.view(member.name, self.region, len(self.sim.store.events) + 1,
                                                exclude_kinds={"agent_trace", "agent_call_failed"})
        context.update(continuous_discussion=True,
                       discussion=[e for e in context["events"] if e["kind"] == "internal_speech"],
                       discussion_revision=self.control["messages"],
                       team_members=[{"id": m.name, "role": m.role.value} for m in self.team],
                       can_finish_discussion=self.can_finish(), finish_requested=self._requested_finish(member, stage),
                       agent_summaries=[e for e in context["events"] if e["kind"] == "round_summary"])
        return context

    def resolve(self, stage: str, actions: dict[str, MemberAction]):
        (name, action), = actions.items()
        member, region, control, store = self.sim.agents[name], self.region, self.control, self.sim.store
        requested_finish = self._requested_finish(member, stage)
        if stage == "internal" and not action.speech.strip():
            raise ValueError("Empty discussion message")
        if stage == "prepare":
            payload, kind = action.model_dump(mode="json"), "individual_preparation"
        elif stage == "internal":
            payload = {"speech": action.speech, "commitment": action.commitment.model_dump(mode="json"),
                       "requested_finish": requested_finish}
            kind = "internal_speech"
        elif stage == "summary":
            payload, kind = {"summary": action.round_summary.model_dump(mode="json")}, "round_summary"
        else:
            payload = {"assignments": [task.model_dump(mode="json") for task in action.action_plan], "next_round": 2}
            kind = "next_round_plan"
        store.append(kind, payload, round=1, visibility="individual" if stage == "prepare" else "delegation",
                     owners=[name] if stage == "prepare" else [region], actor=name)
        if stage == "prepare":
            self.prepared.add(name)
        elif stage == "summary":
            self.summarized.add(name)
            if len(self.summarized) == len(self.team):
                control["phase"] = "plan"
        elif stage == "plan":
            control.update(active=False, phase="completed", status="completed")
            store.append("discussion_closed", {"summaries": len(self.summarized), "next_round": 2},
                         round=1, visibility="delegation", owners=[region], actor=name)
        else:
            if not requested_finish:
                self.cursor += 1
            control["messages"] += 1
            control["remaining"] -= 1
            if action.finish_discussion:
                control.update(phase="summaries", finish_requested=False, finish_resume=False)
                store.append("discussion_ended", {"reason": action.finish_reason},
                             round=1, visibility="delegation", owners=[region], actor=name)
            elif control["remaining"] <= 0:
                control.update(active=False, status="limited")
            elif requested_finish and not control["finish_resume"]:
                control.update(active=False, status="paused")
            if requested_finish:
                control.update(finish_requested=False, finish_resume=False)


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
        # Bilaterals open once every delegation has concluded Round 1; they belong to Round 2.
        self.bilateral = {"active": False, "status": "idle", "thinking_agents": [], "error": None}
        for observer in (self.sim.rapporteur, *self.sim.analysts):
            observer.thinking_level, observer.include_thoughts = thinking_level, True
        if config.bilateral_slots:
            self.sim.bilaterals = AgentAllocator(self.sim.hub, self.sim.store, round_number=2,
                message_limit=config.bilateral_messages)
        self._closed = False
        if saved:
            self.events[:] = [Event.model_validate_json(line) for line in (resume / "events.jsonl").read_text().splitlines()]
            self.sim.store.path = resume / "events.jsonl"
            self.sim.output = resume
            for name, member in self.sim.agents.items():
                state = saved["agents"][name]
                for field, key in (("preferred", "final_preference"), ("initial_preference", "initial_preference")):
                    setattr(member, field, ClimateCommitment.model_validate(state[key]) if state.get(key) else None)
                member.private_strategy = state["private_strategy"]
                member.observed_event_ids = list(state.get("observed_event_ids", []))
                member.action_traces = [json.loads(json.dumps(e.payload)) for e in self.events
                                        if e.kind == "agent_trace" and e.actor == name]
            self.sim.hub.load(self.events)
            if self.sim.bilaterals and any(e.kind == "agent_partner_plan" for e in self.events):
                self.bilateral["status"] = "completed" if self.sim.bilaterals.done else "paused"
        self.discussions = {region: RoundOneDiscussion(self.sim, region, self.controls[region],
                                                       lambda region=region: self._can_finish(region))
                            for region in self.sim.teams}
        if saved:
            for region, control in self.controls.items():
                control.update(saved["live"]["controls"].get(region, {}))
                team_events = [e for e in self.events if e.round == 1 and e.owners == [region]]
                control["messages"] = sum(e.kind == "internal_speech" for e in team_events)
                if any(e.kind == "next_round_plan" for e in team_events):
                    control["phase"] = "completed"
                elif any(e.kind == "discussion_ended" for e in team_events):
                    done = len(self.discussions[region].summarized) == len(self.sim.teams[region])
                    control["phase"] = "plan" if done else "summaries"
                control.update(active=False, thinking_agent=None, stage=None)
                control["status"] = "completed" if control["phase"] == "completed" else "paused" if team_events else "idle"
            self.revision = saved["live"].get("revision", 0) + 1
        self._checkpoint()
        self.loops: dict[str, tuple[StepController, threading.Thread]] = {}
        for region, discussion in self.discussions.items():
            self._start_loop(region, discussion, self.sim.teams[region], self.controls[region], discussion.context)
        if self.sim.bilaterals:
            self.sim.bilaterals.reset_waits()
            controller = StepController(start_paused=True)
            thread = threading.Thread(target=self._allocator_loop, name="bilateral-allocator", daemon=True)
            self.loops["bilaterals"] = (controller, thread)
            thread.start()

    def _allocator_member(self, name):
        return self.sim.agents.get(name) or next(m for m in self.sim.analysts if m.name == name)

    def _allocator_loop(self):
        """Dispatch independently; a slow conversation never holds up free agents or other pairs."""
        schedule = self.sim.bilaterals
        with ThreadPoolExecutor(max_workers=len(self.sim.agents)) as pool:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self._closed or self.bilateral['active'])
                    if self._closed:
                        return
                    due = schedule.due()
                    if schedule.done:
                        self.bilateral.update(active=False, status='completed')
                        self._changed()
                        continue
                    for stage, name in due:
                        schedule.inflight.add(name)
                        context = self._bilateral_context(self._allocator_member(name), stage)
                        pool.submit(self._allocator_call, stage, name, context)
                    self.bilateral.update(status='thinking' if schedule.inflight else 'queued',
                                          thinking_agents=sorted(schedule.inflight))
                    self._changed()
                    self.condition.wait(timeout=.25)

    def _allocator_call(self, stage, name, context):
        member, schedule = self._allocator_member(name), self.sim.bilaterals
        def resolve(actions):
            with self.condition:
                start = len(self.events)
                schedule.resolve(stage, actions)
                trace = member.action_traces[-1]
                trace['resolution_event_ids'] = [e.id for e in self.events[start:] if e.actor == name]
                self.sim.store.append('agent_trace', trace, round=2, visibility='individual', owners=[name], actor=name)
        try:
            run_scene(stage, [member], simultaneous=False, turns=1, context=lambda _:context, resolve=resolve)
        except Exception as error:
            with self.condition:
                self.bilateral.update(active=False, status='error', error=f'{name} failed. Continue to retry.')
                self.sim.store.append('agent_call_failed', {'stage':stage, 'error_type':type(error).__name__,
                    'generation':getattr(getattr(member, 'backend', None), 'last_generation', None)}, round=2,
                    visibility='individual', owners=[name], actor=name)
        finally:
            with self.condition:
                schedule.inflight.discard(name)
                self.bilateral['thinking_agents'] = sorted(schedule.inflight)
                if not self.bilateral['active'] and self.bilateral['status'] not in {'error','completed'}:
                    self.bilateral['status'] = 'pausing' if schedule.inflight else 'paused'
                self._changed()

    def _start_loop(self, key: str, schedule, entities: list[DelegationMember], control: dict,
                    context: Callable[[DelegationMember, str], dict]):
        """One Concordia loop per country, and one for the bilaterals; each waits paused until Start."""
        controller = StepController(start_paused=True)
        members = {m.name: m for m in entities}
        bilateral = "thinking_agents" in control
        state = {"slot": None}

        def acting(due):
            if bilateral:
                state["slot"] = schedule.slot
                control.update(status="thinking", thinking_agents=[name for _, name in due])
            else:
                (stage, name), = due
                control.update(status="thinking", thinking_agent=name, stage=stage)
            self._changed()

        def failed(name, stage, error):
            member = members[name]
            control.update(active=False, status="error", error="Agent call failed. Continue to retry the current step.")
            self.sim.store.append("agent_call_failed", {"stage": stage,
                "error_type": member.last_error or (type(error).__name__ if error else "AgentError"),
                "generation": getattr(getattr(member, "backend", None), "last_generation", None)},
                round=schedule.round, visibility="individual", owners=[name], actor=name)

        def after_step(_):
            with self.condition:
                if bilateral:
                    control["thinking_agents"] = []
                    if control["status"] == "error":
                        pass
                    elif schedule.done:
                        control.update(active=False, status="completed")
                    elif schedule.slot != state["slot"] and not schedule.slots_done:
                        # Stop between slots so the conversations can be read before the next round of requests.
                        control.update(active=False, status="slot_done")
                    else:
                        control["status"] = "queued" if control["active"] else "paused"
                else:
                    control.update(thinking_agent=None, stage=None)
                    if control["active"]:
                        control["status"] = "queued"
                    elif control["status"] not in {"error", "limited", "completed"}:
                        control["status"] = "paused"
                # The loop asks for permission before its next step; an inactive control stops it there.
                if not control["active"]:
                    controller.pause()
                self._changed()

        game_master = ScheduledGameMaster(f"{key} chair", members, schedule,
                                          store=self.sim.store, context=context, lock=self.lock,
                                          on_acting=acting, on_failure=failed)
        thread = threading.Thread(target=run_schedule, args=(game_master, entities), name=f"{key}-loop",
                                  kwargs={"step_controller": controller, "step_callback": after_step}, daemon=True)
        self.loops[key] = (controller, thread)
        thread.start()

    def _changed(self):
        self.revision += 1
        self._checkpoint()
        self.condition.notify_all()

    def _bilateral_context(self, member: DelegationMember, stage: str) -> dict:
        self.sim.current_round = 2
        if stage in (SUMMARY_STAGE, RELATIONS_STAGE):
            return self.sim.bilateral_context(member, stage)
        context = self.sim.context(member, stage)
        context["events"] = self.sim.store.view(member.name, member.brief.delegation.value, len(self.events) + 1,
                                              exclude_kinds={"agent_trace", "agent_call_failed"})
        context['allocator_mode'] = True
        region = member.brief.delegation.value
        context['team_members'] = [{'id':m.name, 'role':m.role.value} for m in self.sim.teams.get(region, [])]
        context['next_round_tasks'] = [task for e in context['events'] if e['kind'] == 'next_round_plan'
                                      for task in e['payload']['assignments'] if task['agent_id'] == member.name]
        return context

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
                    "allocator": True, "max_partners": 5, "wait_seconds": 10,
                    "message_limit": self.sim.config.bilateral_messages, "done": bool(meetings and meetings.done)}
                result['queue'] = meetings.snapshot() if meetings else []
            return result

    def _bilaterals_open(self):
        return all(c["phase"] == "completed" for c in self.controls.values())

    def _can_finish(self, region):
        speakers = {e.actor for e in self.events if e.kind == "internal_speech"
                    and e.round == 1 and e.owners == [region]}
        return self.controls[region]["phase"] == "discussion" and all(
            member.name in speakers for member in self.sim.teams[region])

    def _act_bilaterals(self, operation):
        control, meetings = self.bilateral, self.sim.bilaterals
        if meetings is None:
            raise ValueError("Bilaterals are turned off for this session")
        controller = self.loops["bilaterals"][0]
        if operation == "start":
            if not self._bilaterals_open():
                raise ValueError("Bilaterals open once every delegation has concluded Round 1")
            if meetings.done:
                raise ValueError("Every agent has finished its conversation plan")
            if not control["active"]:
                meetings.reset_waits()
                control.update(active=True, error=None, status="thinking" if control["thinking_agents"] else "queued")
            controller.play()
        elif operation == "pause":
            control.update(active=False, status="pausing" if control["thinking_agents"] else "paused")
            controller.pause()
        else:
            raise ValueError("Use start or pause for bilaterals")
        self._changed()
        return dict(control)

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
            controller = self.loops[region][0]
            operation = action.get("operation")
            if control["phase"] == "completed":
                raise ValueError("This meeting has concluded; its summaries and action plan are final")
            if operation == "start":
                if not control["active"]:
                    control.update(active=True, remaining=self.message_budget, error=None)
                    control["status"] = "thinking" if control["thinking_agent"] else "queued"
                controller.play()
            elif operation == "pause":
                control.update(active=False, status="pausing" if control["thinking_agent"] else "paused")
                controller.pause()
            elif operation == "finish":
                if not self._can_finish(region):
                    raise ValueError("Every member must contribute before asking the head to conclude")
                if not control["finish_requested"]:
                    control.update(finish_requested=True, finish_resume=control["active"], active=True,
                                   remaining=max(1, control["remaining"]), error=None)
                    if not control["thinking_agent"]:
                        control["status"] = "queued"
                    controller.play()
            else:
                raise ValueError("Use start, pause or finish for live conversations")
            self._changed()
            return {**control, "can_finish": self._can_finish(region)}

    def close(self):
        with self.condition:
            self._closed = True
            for control in (*self.controls.values(), self.bilateral):
                control["active"] = False
            for controller, _ in self.loops.values():
                controller.stop()
            self.condition.notify_all()
        # A bounded join; an in-flight provider request may still be finishing.
        for _, thread in self.loops.values():
            thread.join(timeout=1)
