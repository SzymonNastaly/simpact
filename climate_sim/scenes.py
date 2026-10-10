"""Deterministic game masters run by Concordia's engines: fixed scenes, and long-running schedules."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from concordia.components.game_master import event_resolution
from concordia.environment.engines.asynchronous import Asynchronous
from concordia.environment.engines.sequential import Sequential
from concordia.environment.engines.simultaneous import Simultaneous
from concordia.environment.step_controller import StepController, StepData
from concordia.typing import entity
from concordia.utils.async_measurements import ReactiveMeasurements

from .agents import DelegationMember
from .memory import EventStore
from .models import MemberAction


class SceneGameMaster(entity.Entity):
    """Only scene participants can act or receive observations. Never models climate."""

    def __init__(self, stage: str, participants: list[DelegationMember], *, steps: int,
                 simultaneous: bool, context: Callable[[DelegationMember], dict],
                 resolve: Callable[[dict[str, MemberAction]], None]):
        self.stage, self.participants = stage, {m.name: m for m in participants}
        self.steps, self.simultaneous = steps, simultaneous
        self.context, self.on_resolve = context, resolve
        self.cursor = 0
        self._pending = ""
        self._barrier_views = {m.name: context(m) for m in participants} if simultaneous else None

    @property
    def name(self):
        return "UN Secretary-General"

    def observe(self, observation: str) -> None:
        prefix = event_resolution.PUTATIVE_EVENT_TAG + " "
        if observation.startswith(prefix):
            self._pending = observation[len(prefix):]

    def act(self, action_spec: entity.ActionSpec = entity.DEFAULT_ACTION_SPEC) -> str:
        kind = action_spec.output_type
        if kind == entity.OutputType.TERMINATE:
            return "Yes" if self.cursor >= self.steps else "No"
        if kind == entity.OutputType.NEXT_ACTING:
            names = list(self.participants)
            return ",".join(names) if self.simultaneous else names[self.cursor % len(names)]
        if kind == entity.OutputType.NEXT_ACTION_SPEC:
            return json.dumps(entity.free_action_spec(call_to_action=f"Perform {self.stage} and return MemberAction JSON.", tag=self.stage).to_dict())
        if kind == entity.OutputType.MAKE_OBSERVATION:
            name = action_spec.call_to_action.removeprefix("observe:")
            member = self.participants[name]
            view = self._barrier_views[name] if self._barrier_views is not None else self.context(member)
            return json.dumps(view, ensure_ascii=False)
        if kind == entity.OutputType.RESOLVE:
            actions = {}
            for line in self._pending.splitlines():
                name, separator, raw = line.partition(": ")
                if not separator or name not in self.participants or name in actions:
                    raise ValueError("Malformed or impersonated scene action")
                actions[name] = MemberAction.model_validate_json(raw)
            expected = set(self.participants) if self.simultaneous else {list(self.participants)[self.cursor % len(self.participants)]}
            if set(actions) != expected:
                raise RuntimeError("A scene participant failed; refusing a partial submission")
            # Sort resolution so parallel actor scheduling cannot perturb seeded offline runs.
            self.on_resolve({name: actions[name] for name in sorted(actions)})
            self.cursor += 1
            return "Actions validated and recorded within their scene visibility."
        raise ValueError(f"Unsupported GM action {kind}")


def run_scene(stage: str, participants: list[DelegationMember], *, simultaneous: bool,
              turns: int, context: Callable[[DelegationMember], dict],
              resolve: Callable[[dict[str, MemberAction]], None]) -> None:
    if not participants or len({member.name for member in participants}) != len(participants):
        raise ValueError("Scenes require distinct named participants")
    steps = turns if simultaneous else turns * len(participants)
    game_master = SceneGameMaster(stage, participants, steps=steps, simultaneous=simultaneous,
                                 context=context, resolve=resolve)
    engine_class = Simultaneous if simultaneous else Sequential
    engine = engine_class(call_to_make_observation="observe:{name}")
    engine.run_loop(game_masters=[game_master], entities=participants, max_steps=steps)
    if game_master.cursor != steps:
        raise RuntimeError("Concordia scene ended before all planned actions resolved")


FAILED_ACTION = "__failed__"


class ScheduledGameMaster(entity.Entity):
    """A long-running game master that follows a schedule instead of a fixed scene.

    The schedule decides who acts next and in which stage (`due()`), and what each action causes
    (`resolve(stage, actions)`); Concordia's engine runs the loop until the schedule is done. Each
    actor's resolution is recorded with its decision trace. Without `on_failure`, a member who fails
    to act stops the run; with it, the failure is reported and the member stays due.

    Under the Simultaneous engine (run_schedule) every due member acts in one step. Under the
    Asynchronous engine (run_async_schedule) each entity runs its own loop and asks independently;
    the game master then hands each member one assignment at a time, tracked in schedule.inflight,
    so nobody waits for a round or for another conversation.
    """

    def __init__(self, name: str, members: dict[str, DelegationMember], schedule, *, store: EventStore,
                 context: Callable[[DelegationMember, str], dict], lock: threading.RLock | None = None,
                 on_acting: Callable[[list[tuple[str, str]]], None] | None = None,
                 on_failure: Callable[[str, str, Exception | None], None] | None = None,
                 concurrent: bool = False):
        self._name, self.members, self.schedule, self.store = name, members, schedule, store
        self.context, self.lock = context, lock or threading.RLock()
        self.on_acting, self.on_failure, self.concurrent = on_acting, on_failure, concurrent
        self._assigned: dict[str, str] = {}
        # Each entity loop resolves its own putative event, so the pending event is per thread.
        self._local = threading.local()
        self.measurements = ReactiveMeasurements()
        self.stopped = False

    @property
    def name(self):
        return self._name

    def observe(self, observation: str) -> None:
        prefix = event_resolution.PUTATIVE_EVENT_TAG + " "
        if observation.startswith(prefix):
            self._local.pending = observation[len(prefix):]

    def _next_acting(self, options: tuple[str, ...]) -> str:
        if self.concurrent and len(options) != 1:
            # The engine's setup pass asks about everyone at once; there is no setup step here.
            return ""
        due = self.schedule.due()
        if self.concurrent:
            # Only the asking entity, and only if it has no assignment in flight.
            due = [(stage, name) for stage, name in due if name in options and name not in self._assigned]
            for _, name in due:
                self.schedule.inflight.add(name)
        else:
            self._assigned = {}
        self._assigned.update({name: stage for stage, name in due})
        if due and self.on_acting:
            self.on_acting(due)
        return ",".join(name for _, name in due)

    def act(self, action_spec: entity.ActionSpec = entity.DEFAULT_ACTION_SPEC) -> str:
        kind = action_spec.output_type
        with self.lock:
            if kind == entity.OutputType.TERMINATE:
                finished = self.stopped or (self.schedule.done if self.concurrent else not self.schedule.due())
                return "Yes" if finished else "No"
            if kind == entity.OutputType.NEXT_ACTING:
                return "" if self.stopped else self._next_acting(tuple(action_spec.options))
            if kind == entity.OutputType.NEXT_ACTION_SPEC:
                stage = self._assigned[action_spec.call_to_action.removeprefix("spec:")]
                return json.dumps(entity.free_action_spec(call_to_action=f"Perform {stage} and return MemberAction JSON.", tag=stage).to_dict())
            if kind == entity.OutputType.MAKE_OBSERVATION:
                name = action_spec.call_to_action.removeprefix("observe:")
                return json.dumps(self.context(self.members[name], self._assigned[name]), ensure_ascii=False)
            if kind == entity.OutputType.RESOLVE:
                actions, failed = {}, set()
                for line in getattr(self._local, "pending", "").splitlines():
                    name, separator, raw = line.partition(": ")
                    if not separator or name not in self._assigned or name in actions or name in failed:
                        raise ValueError("Malformed or impersonated scheduled action")
                    if raw == FAILED_ACTION:
                        failed.add(name)
                    else:
                        actions[name] = MemberAction.model_validate_json(raw)
                # Concurrently, other entities' assignments are still in flight; only these lines resolve.
                names = sorted(actions.keys() | failed) if self.concurrent else sorted(self._assigned)
                # Sorted, so parallel scheduling cannot perturb seeded offline runs.
                for name in names:
                    stage = self._assigned.pop(name)
                    getattr(self.schedule, "inflight", set()).discard(name)
                    if name not in actions:
                        self._fail(name, stage, None)
                        continue
                    start = len(self.store.events)
                    try:
                        self.schedule.resolve(stage, {name: actions[name]})
                    except Exception as error:
                        self._fail(name, stage, error)
                        continue
                    trace = self.members[name].action_traces[-1]
                    trace["resolution_event_ids"] = [e.id for e in self.store.events[start:] if e.actor == name]
                    self.store.append("agent_trace", trace, round=self.schedule.round, visibility="individual",
                                      owners=[name], actor=name)
                self._local.pending = ""
                return "Actions validated and recorded within their visibility."
        raise ValueError(f"Unsupported GM action {kind}")

    def _fail(self, name: str, stage: str, error: Exception | None):
        if self.on_failure is None:
            raise RuntimeError(f"{name} failed during {stage}") from error
        self.on_failure(name, stage, error)


class _Resilient:
    """An entity as the Asynchronous engine sees it: a failed call becomes a FAILED_ACTION for the game
    master to report, instead of an exception that would end that entity's loop for the session."""

    def __init__(self, entity_: entity.Entity):
        self._entity, self.measurements = entity_, ReactiveMeasurements()

    @property
    def name(self):
        return self._entity.name

    def observe(self, observation: str) -> None:
        self._entity.observe(observation)

    def act(self, action_spec: entity.ActionSpec = entity.DEFAULT_ACTION_SPEC) -> str:
        try:
            return self._entity.act(action_spec)
        except Exception:
            return f"{self.name}: {FAILED_ACTION}"


def run_schedule(game_master: ScheduledGameMaster, entities: list[DelegationMember], *,
                 step_controller: StepController | None = None,
                 step_callback: Callable[[StepData], None] | None = None) -> None:
    """Run a scheduled game master in steps until nobody is due, or until the step controller stops it."""
    engine = Simultaneous(call_to_make_observation="observe:{name}", call_to_next_action_spec="spec:{name}")
    engine.run_loop(game_masters=[game_master], entities=entities, max_steps=1_000_000,
                    step_controller=step_controller, step_callback=step_callback)


def run_async_schedule(game_master: ScheduledGameMaster, entities: list[DelegationMember], engine: Asynchronous, *,
                       step_controller: StepController | None = None,
                       step_callback: Callable[[StepData], None] | None = None) -> None:
    """Run a concurrent scheduled game master with every entity in its own Concordia loop, until done."""
    engine.run_loop(game_masters=[game_master], entities=[_Resilient(e) for e in entities], max_steps=10**9,
                    step_controller=step_controller, step_callback=step_callback)


def async_engine(sleep_time: float = .5) -> Asynchronous:
    """Idle entities re-ask the game master every sleep_time seconds."""
    return Asynchronous(call_to_make_observation="observe:{name}", call_to_next_action_spec="spec:{name}",
                        sleep_time=sleep_time)
