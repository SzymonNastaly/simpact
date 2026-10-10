"""One privileged audit store; agents receive only copied, authorized views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from .models import Model


class Event(Model):
    id: str
    round: int
    kind: str
    visibility: Literal["individual", "delegation", "public"]
    owners: list[str] = Field(default_factory=list)
    actor: str | None = None
    payload: dict[str, Any]

    @model_validator(mode="after")
    def scoped(self):
        if self.visibility == "public" and self.owners:
            raise ValueError("Public events cannot have private owners")
        if self.visibility != "public" and not self.owners:
            raise ValueError("Private events require explicit owners")
        return self

    def visible_to(self, agent_id: str, delegation: str) -> bool:
        return (self.visibility == "public"
                or (self.visibility == "individual" and agent_id in self.owners)
                or (self.visibility == "delegation" and delegation in self.owners))


class EventStore:
    def __init__(self, path: Path | None = None):
        self.events: list[Event] = []
        self.path = path
        if path is not None:
            path.touch(mode=0o600, exist_ok=False)

    def append(self, kind: str, payload: dict, *, round: int, visibility: str,
               owners: list[str] | None = None, actor: str | None = None) -> Event:
        event = Event(id=f"event-{len(self.events) + 1:06d}", round=round, kind=kind,
                      visibility=visibility, owners=owners or [], actor=actor, payload=payload)
        self.events.append(event)
        if self.path:
            with self.path.open("a") as stream:
                stream.write(event.model_dump_json() + "\n")
        return event

    def view(self, agent_id: str, delegation: str, limit: int) -> list[dict]:
        # Copy at the boundary: mutating an agent's view cannot mutate another agent's memory.
        return [event.model_dump(mode="json") for event in self.events
                if event.visible_to(agent_id, delegation)][-limit:]

    def public_view(self) -> list[dict]:
        return [event.model_dump(mode="json") for event in self.events if event.visibility == "public"]


def write_json(path: Path, data: Any) -> None:
    with path.open("x") as stream:
        path.chmod(0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
