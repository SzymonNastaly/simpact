"""Participant-scoped, tick-based meeting protocol, independent of LLM controllers.

Any registered member can invite any other member, including a multi-country group.
All invitees must acknowledge before conversation starts. Tick five is the default
deadline (not five seconds). Missing acknowledgements terminate the request.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .memory import EventStore
from .models import Model


class Participant(Model):
    id: str
    delegation: str
    role: str
    controller: Literal["agent", "human"] = "agent"
    model: str | None = None


class Meeting(Model):
    id: str
    sender: str
    invitees: list[str] = Field(min_length=1)
    topic: str = Field(min_length=1, max_length=300)
    created_tick: int = Field(ge=0)
    expires_tick: int = Field(ge=1)
    acknowledgements: dict[str, Literal["pending", "accepted", "declined"]]
    status: Literal["pending", "active", "declined", "expired", "closed"] = "pending"
    messages: list[dict] = Field(default_factory=list)
    round: int = Field(default=1, ge=1)
    reports: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_members(self):
        if self.sender in self.invitees or len(set(self.invitees)) != len(self.invitees):
            raise ValueError("Invitees must be distinct and cannot include the sender")
        if set(self.acknowledgements) != set(self.invitees):
            raise ValueError("Every invitee needs an acknowledgement state")
        if self.expires_tick <= self.created_tick:
            raise ValueError("A meeting deadline must follow its creation tick")
        return self

    @property
    def participants(self) -> list[str]:
        return [self.sender, *self.invitees]


class MeetingHub:
    def __init__(self, participants: list[Participant], *, acknowledgement_ticks: int = 5,
                 store: EventStore | None = None, round_number: int = 1):
        if acknowledgement_ticks < 1:
            raise ValueError("Acknowledgement window must be positive")
        self.participants = {p.id: p.model_copy(deep=True) for p in participants}
        if len(self.participants) != len(participants):
            raise ValueError("Participant IDs must be unique")
        self.acknowledgement_ticks = acknowledgement_ticks
        self.tick = 0
        self.round_number = round_number
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

    def _record(self, kind: str, meeting: Meeting, actor: str | None = None):
        # Individual scope lists exact participants, never whole delegations.
        self.store.append(kind, {"tick": self.tick, "meeting": meeting.model_dump(mode="json")},
                          round=meeting.round, visibility="individual",
                          owners=meeting.participants, actor=actor)

    def request(self, actor: str, invitees: list[str], topic: str) -> Meeting:
        self._known(actor)
        for invitee in invitees:
            self._known(invitee)
        meeting = Meeting(id=f"meeting-{len(self.meetings) + 1:04d}", sender=actor,
            invitees=invitees, topic=topic.strip(), created_tick=self.tick,
            round=self.round_number,
            expires_tick=self.tick + self.acknowledgement_ticks,
            acknowledgements={p: "pending" for p in invitees})
        self.meetings[meeting.id] = meeting
        self._record("meeting_requested", meeting, actor)
        return meeting.model_copy(deep=True)

    def respond(self, actor: str, meeting_id: str, accepted: bool) -> Meeting:
        meeting = self._visible(actor, meeting_id)
        if actor not in meeting.invitees:
            raise PermissionError("Only an invitee can acknowledge")
        if meeting.status != "pending" or self.tick >= meeting.expires_tick:
            raise ValueError("Meeting request is no longer awaiting acknowledgements")
        if meeting.acknowledgements[actor] != "pending":
            raise ValueError("A participant can acknowledge only once")
        meeting.acknowledgements[actor] = "accepted" if accepted else "declined"
        if not accepted:
            meeting.status = "declined"
        elif all(state == "accepted" for state in meeting.acknowledgements.values()):
            meeting.status = "active"
        self._record("meeting_acknowledged" if accepted else "meeting_declined", meeting, actor)
        return meeting.model_copy(deep=True)

    def message(self, actor: str, meeting_id: str, text: str) -> Meeting:
        meeting = self._visible(actor, meeting_id)
        if meeting.status != "active":
            raise ValueError("Conversation requires an active, fully acknowledged meeting")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            raise ValueError("A message must contain 1..4000 characters")
        meeting.messages.append({"id": f"{meeting.id}-message-{len(meeting.messages) + 1:04d}",
                                 "actor": actor, "tick": self.tick, "text": text.strip()})
        self._record("meeting_message", meeting, actor)
        return meeting.model_copy(deep=True)

    def close(self, actor: str, meeting_id: str) -> Meeting:
        meeting = self._visible(actor, meeting_id)
        if meeting.status not in ("active", "pending"):
            raise ValueError("Meeting is already terminal")
        meeting.status = "closed"
        self._record("meeting_closed", meeting, actor)
        return meeting.model_copy(deep=True)

    def report(self, actor: str, meeting_id: str, summary: str, *,
               agreements: list[str] | None = None, next_steps: list[str] | None = None,
               notify_team: bool = True) -> dict:
        meeting = self._visible(actor, meeting_id)
        if meeting.status not in ("active", "closed"):
            raise ValueError("Only an acknowledged conversation can have an outcome report")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 1200:
            raise ValueError("Outcome summary requires 1..1200 characters")
        if any(r["author"] == actor for r in meeting.reports):
            raise ValueError("This participant already reported this conversation")
        for item in [*(agreements or []), *(next_steps or [])]:
            if not isinstance(item, str) or len(item) > 1200:
                raise ValueError("Report items must be short strings")
        report = {"id": f"{meeting_id}-report-{len(meeting.reports)+1:03d}",
                  "meeting_id": meeting_id, "author": actor, "tick": self.tick,
                  "round": meeting.round, "summary": summary.strip(),
                  "agreements": agreements or [], "next_steps": next_steps or [],
                  "interpretation": "Participant-authored report, not independently verified agreement",
                  "notified_delegation": self.participants[actor].delegation if notify_team else None}
        meeting.reports.append(report)
        self._record("meeting_outcome_reported", meeting, actor)
        if notify_team:
            self.store.append("team_meeting_report", report, round=meeting.round,
                visibility="delegation", owners=[self.participants[actor].delegation], actor=actor)
        return dict(report)

    def advance(self, ticks: int = 1):
        if not isinstance(ticks, int) or isinstance(ticks, bool) or not 1 <= ticks <= 100:
            raise ValueError("Advance requires 1..100 integer ticks")
        # Emit at the actual expiry tick even when several ticks are advanced at once.
        for _ in range(ticks):
            self.tick += 1
            for meeting in self.meetings.values():
                if meeting.status == "pending" and self.tick >= meeting.expires_tick:
                    meeting.status = "expired"
                    self._record("meeting_expired", meeting)

    def view(self, actor: str | None = None, *, researcher: bool = False) -> dict:
        if not researcher:
            self._known(actor)
        notifications = [e.model_dump(mode="json") for e in self.store.events
                         if e.kind == "team_meeting_report" and
                         (researcher or e.owners == [self.participants[actor].delegation])]
        return {"tick": self.tick, "acknowledgement_ticks": self.acknowledgement_ticks,
                "notifications": notifications,
                "participants": [p.model_dump(mode="json") for p in self.participants.values()],
                "meetings": [m.model_dump(mode="json") for m in self.meetings.values()
                             if researcher or actor in m.participants]}
