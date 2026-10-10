"""A deterministic UN Game Master using the real Concordia scene engines."""

import json
from collections.abc import Callable

from concordia.components.game_master import event_resolution
from concordia.environment.engines.sequential import Sequential
from concordia.environment.engines.simultaneous import Simultaneous
from concordia.typing import entity

from .agents import DelegationMember
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
