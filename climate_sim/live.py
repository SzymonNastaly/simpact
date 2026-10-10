"""Continuous Round 1 conversations. One worker, one fresh context per action.

The worker never holds the viewer lock during network calls. A pause allows the
in-flight action to finish, then prevents further calls. No calls on page load.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Callable

from .agents import Backend, GeminiBackend, OfflineBackend
from .lab import Lab
from .models import SimulationConfig
from .scenes import run_scene
from .simulation import Simulation


class LiveLab(Lab):
    def __init__(self, config: SimulationConfig, *, output: Path | None = None,
                 thinking_level: str = "medium", message_budget: int = 12,
                 backend_factory: Callable[[str], Backend] | None = None):
        if not 1 <= message_budget <= 100:
            raise ValueError("Message budget must be between 1 and 100")
        if thinking_level not in {"low", "medium", "high"}:
            raise ValueError("Unknown thinking level")
        if backend_factory is None:
            backend_factory = lambda name: (OfflineBackend() if config.backend == "offline" else
                GeminiBackend(config.agent_models.get(name, config.default_model),
                              thinking_level=thinking_level, include_thoughts=True, max_output_tokens=8192))
        self.sim = Simulation(config, output=output, backend_factory=backend_factory)
        self.events, self.briefs, self.hub = self.sim.store.events, self.sim.briefs, self.sim.hub
        self.output, self.human_role = output, None
        self.run_name = output.name if output else "live-round-one"
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.message_budget, self.thinking_level = message_budget, thinking_level
        self.revision = 0
        self.controls = {region: {"active": False, "status": "idle", "thinking_agent": None,
                                 "stage": None, "messages": 0, "remaining": 0, "error": None}
                         for region in self.sim.teams}
        self.prepared: set[str] = set()
        self.cursor = {region: 0 for region in self.sim.teams}
        self._region_cursor = 0
        self._closed = False
        self._checkpoint()
        self.worker = threading.Thread(target=self._work, name="round-one-agent-loop", daemon=True)
        self.worker.start()

    def _checkpoint(self):
        self.state = {"config": self.sim.config.model_dump(mode="json"),
                      "agents": {name: member.save_state() for name, member in self.sim.agents.items()},
                      "outcomes_by_round": {}, "commitments_by_round": {},
                      "live": {"round": 1, "controls": self.controls, "revision": self.revision,
                               "thinking_level": self.thinking_level}}
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
            return result

    def act(self, action):
        with self.condition:
            if self._closed:
                raise ValueError("Live session is closed")
            if self.human_role:
                raise PermissionError("Live controls require the researcher view")
            region = action.get("country")
            if region not in self.controls:
                raise ValueError("Unknown country")
            control = self.controls[region]
            operation = action.get("operation")
            if operation == "start":
                if not control["active"]:
                    control.update(active=True, remaining=self.message_budget, error=None)
                    control["status"] = "thinking" if control["thinking_agent"] else "queued"
            elif operation == "pause":
                control.update(active=False, status="pausing" if control["thinking_agent"] else "paused")
            else:
                raise ValueError("Use start or pause for live conversations")
            self.revision += 1
            self._checkpoint()
            self.condition.notify_all()
            return dict(control)

    def _next(self):
        regions = list(self.controls)
        for offset in range(len(regions)):
            index = (self._region_cursor + offset) % len(regions)
            region = regions[index]
            control = self.controls[region]
            if control["active"]:
                self._region_cursor = (index + 1) % len(regions)
                team = self.sim.teams[region]
                unprepared = next((m for m in team if m.name not in self.prepared), None)
                member = unprepared or team[self.cursor[region] % len(team)]
                stage = "prepare" if unprepared else "internal"
                control.update(status="thinking", thinking_agent=member.name, stage=stage)
                self.revision += 1
                return region, member, stage
        return None

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self._closed or any(c["active"] for c in self.controls.values()))
                if self._closed:
                    return
                region, member, stage = self._next()
                self.sim.stage = stage
                context = self.sim.context(member)
                # Full scoped history: never truncate away messages the agent is responding to.
                context["events"] = self.sim.store.view(member.name, region, len(self.events) + 1,
                                                      exclude_kinds={"agent_trace", "agent_call_failed"})
                context.update(continuous_discussion=True,
                               discussion=[e for e in context["events"] if e["kind"] == "internal_speech"],
                               discussion_revision=self.controls[region]["messages"],
                               team_members=[{"id": m.name, "role": m.role.value} for m in self.sim.teams[region]])

            def resolve(actions):
                with self.condition:
                    action = actions[member.name]
                    if stage == "internal" and not action.speech.strip():
                        raise ValueError("Empty discussion message")
                    if stage == "prepare":
                        payload = action.model_dump(mode="json")
                    else:
                        payload = {"speech": action.speech, "commitment": action.commitment.model_dump(mode="json")}
                    event = self.sim.store.append("individual_preparation" if stage == "prepare" else "internal_speech",
                        payload, round=1, visibility="individual" if stage == "prepare" else "delegation",
                        owners=[member.name] if stage == "prepare" else [region], actor=member.name)
                    trace = member.action_traces[-1]
                    trace["resolution_event_ids"] = [event.id]
                    self.sim.store.append("agent_trace", trace, round=1, visibility="individual",
                                          owners=[member.name], actor=member.name)
                    if stage == "prepare":
                        self.prepared.add(member.name)
                    else:
                        self.cursor[region] += 1
                        self.controls[region]["messages"] += 1
                        self.controls[region]["remaining"] -= 1
                        if self.controls[region]["remaining"] <= 0:
                            self.controls[region].update(active=False, status="limited")
                    self.revision += 1
                    self._checkpoint()

            try:
                # Retain Concordia's actual observation/action/validation/resolution path.
                run_scene(stage, [member], simultaneous=False, turns=1, context=lambda _: context, resolve=resolve)
            except Exception as error:
                with self.condition:
                    self.controls[region].update(active=False, status="error",
                        error="Agent call failed. Inspect the call record, then Continue to retry.")
                    self.sim.store.append("agent_call_failed", {"stage": stage, "error_type": type(error).__name__,
                        "generation": getattr(member.backend, "last_generation", None)},
                        round=1, visibility="individual", owners=[member.name], actor=member.name)
            finally:
                with self.condition:
                    control = self.controls[region]
                    control.update(thinking_agent=None, stage=None)
                    if control["active"]:
                        control["status"] = "queued"
                    elif control["status"] not in {"error", "limited"}:
                        control["status"] = "paused"
                    self.revision += 1
                    self._checkpoint()
                    self.condition.notify_all()

    def close(self):
        with self.condition:
            self._closed = True
            for control in self.controls.values():
                control["active"] = False
            self.condition.notify_all()
        # A bounded join; an in-flight provider request may still be finishing.
        self.worker.join(timeout=1)
