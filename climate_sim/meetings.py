"""Registry of one-to-one bilateral conversations, independent of LLM controllers.

The game master opens a meeting once it has paired two members (see bilaterals.py).
Only the two participants ever observe a meeting; each participant's report reaches
its own delegation only. Events carry single messages, so a long conversation does
not repeat its transcript in every observation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .memory import Event, EventStore
from .models import MeetingReport, Model


class Participant(Model):
    id: str
    delegation: str
    role: str
    model: str | None = None


class Meeting(Model):
    id: str
    round: int = Field(ge=1)
    slot: int = Field(ge=1)
    initiator: str
    partner: str
    topic: str = Field(min_length=1, max_length=300)
    # Both asked for this meeting; otherwise only the initiator did, and the partner may decline.
    mutual: bool
    status: Literal["active", "closed"] = "active"
    messages: list[dict] = Field(default_factory=list)
    closed_by: str | None = None
    close_reason: Literal["ended", "limit"] | None = None

    @model_validator(mode="after")
    def two_people(self):
        if self.initiator == self.partner:
            raise ValueError("A bilateral needs two different members")
        return self

    @property
    def participants(self) -> list[str]:
        return [self.initiator, self.partner]


class MeetingHub:
    def __init__(self, participants: list[Participant], *, store: EventStore | None = None):
        self.participants = {p.id: p.model_copy(deep=True) for p in participants}
        if len(self.participants) != len(participants):
            raise ValueError("Participant IDs must be unique")
        self.meetings: dict[str, Meeting] = {}
        self.store = store or EventStore()

    def _known(self, actor: str):
        if actor not in self.participants:
            raise PermissionError("Unknown participant")

    def _visible(self, actor: str, meeting_id: str) -> Meeting:
        self._known(actor)
        meeting = self.meetings.get(meeting_id)
        if meeting is None or actor not in meeting.participants:
            raise PermissionError("Meeting is not visible to this participant")
        return meeting

    def _record(self, kind: str, meeting: Meeting, payload: dict, actor: str | None):
        self.store.append(kind, payload, round=meeting.round, visibility="individual",
                          owners=meeting.participants, actor=actor)

    def open(self, initiator: str, partner: str, topic: str, *, round_number: int, slot: int, mutual: bool) -> Meeting:
        self._known(initiator)
        self._known(partner)
        meeting = Meeting(id=f"meeting-{len(self.meetings) + 1:04d}", round=round_number, slot=slot,
                          initiator=initiator, partner=partner, topic=topic.strip(), mutual=mutual)
        self.meetings[meeting.id] = meeting
        self._record("meeting_opened", meeting, {"meeting": meeting.model_dump(mode="json", exclude={"messages"})}, initiator)
        return meeting.model_copy(deep=True)

    def message(self, actor: str, meeting_id: str, text: str) -> dict:
        meeting = self._visible(actor, meeting_id)
        if meeting.status != "active":
            raise ValueError("This conversation has ended")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            raise ValueError("A message must contain 1..4000 characters")
        message = {"id": f"{meeting.id}-message-{len(meeting.messages) + 1:02d}", "actor": actor, "text": text.strip()}
        meeting.messages.append(message)
        self._record("meeting_message", meeting, {"meeting_id": meeting.id, "message": message}, actor)
        return dict(message)

    def close(self, meeting_id: str, *, reason: Literal["ended", "limit"], actor: str | None = None) -> Meeting:
        meeting = self.meetings[meeting_id] if actor is None else self._visible(actor, meeting_id)
        if meeting.status == "closed":
            raise ValueError("Meeting is already closed")
        meeting.status, meeting.closed_by, meeting.close_reason = "closed", actor, reason
        self._record("meeting_closed", meeting, {"meeting_id": meeting.id, "closed_by": actor, "reason": reason}, actor)
        return meeting.model_copy(deep=True)

    def report(self, actor: str, meeting_id: str, report: MeetingReport) -> dict:
        meeting = self._visible(actor, meeting_id)
        if meeting.status != "closed":
            raise ValueError("Report on a conversation once it has ended")
        delegation = self.participants[actor].delegation
        payload = {"meeting_id": meeting.id, "round": meeting.round, "slot": meeting.slot,
                   "partner": next(p for p in meeting.participants if p != actor), "topic": meeting.topic,
                   **report.model_dump(mode="json"),
                   "interpretation": "Participant-authored report, not independently verified agreement"}
        # The partner never sees this account; the reporter's own delegation does.
        self.store.append("team_meeting_report", payload, round=meeting.round,
                          visibility="delegation", owners=[delegation], actor=actor)
        return payload

    def load(self, events: list[Event]):
        """Rebuild the registry from recorded events, e.g. when a saved live session resumes."""
        for item in fold_meetings(events).values():
            item.pop("reports")
            item["messages"] = [{k: v for k, v in m.items() if k != "event_id"} for m in item["messages"]]
            self.meetings[item["id"]] = Meeting.model_validate(item)


def fold_meetings(events: list[Event]) -> dict[str, dict]:
    """Meetings with their messages and each side's report, from whichever events the caller may see."""
    meetings: dict[str, dict] = {}
    for event in events:
        payload = event.payload
        if event.kind == "meeting_opened" and "meeting" in payload:
            meetings[payload["meeting"]["id"]] = {**payload["meeting"], "messages": [], "reports": {}}
            continue
        # Older runs used a different meeting protocol; their events are skipped.
        meeting = meetings.get(payload.get("meeting_id"))
        if meeting is None:
            continue
        if event.kind == "meeting_message":
            meeting["messages"].append({**payload["message"], "event_id": event.id})
        elif event.kind == "meeting_closed":
            meeting.update(status="closed", closed_by=payload["closed_by"], close_reason=payload["reason"])
        elif event.kind == "team_meeting_report":
            meeting["reports"][event.actor] = {**payload, "event_id": event.id}
    return meetings
