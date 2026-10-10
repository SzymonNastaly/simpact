"""Bilaterals: private one-to-one conversations between individual members, partners chosen by the members.

A round of bilaterals has one or more slots. In each slot:

1. nominate: every member and observer names up to three people it wants to meet, most wanted first.
2. The game master pairs them (pair_requests): pairs across delegations first, then mutual requests,
   then the better combined rank. Nobody is in two meetings at once, and no pair meets twice in a round.
3. bilateral: each pair takes turns, opener first, until one side sets end_conversation or the message
   limit is reached. A partner who did not ask for the meeting can decline on its first turn.
4. debrief: once its conversation ends, each participant reports to its own team.
5. slot_summary: once every conversation has ended and been reported, a neutral rapporteur
   summarizes the slot for the researcher. Agents never see it.

State is read from the event store and meeting registry rather than kept here, so a resumed live
session continues where it stopped. Any subset of due actions can be resolved: the batch simulation
resolves a whole step at once, the live viewer resolves each call as it returns.
"""

from __future__ import annotations

from .meetings import Meeting, MeetingHub
from .memory import EventStore
from .models import MemberAction, PartnerRequest

STAGES = ("nominate", "bilateral", "debrief")
SUMMARY_STAGE, RAPPORTEUR = "slot_summary", "rapporteur"


def pair_requests(nominations: dict[str, list[PartnerRequest]], delegation_of: dict[str, str],
                  met: set[frozenset[str]]) -> list[tuple[str, str, str, bool]]:
    """Greedy matching over requested pairs, as (opener, partner, topic, mutual) tuples."""
    requested: dict[frozenset[str], dict[str, tuple[int, str]]] = {}
    for sender, requests in nominations.items():
        for rank, request in enumerate(requests):
            pair = frozenset((sender, request.member))
            if pair not in met:
                requested.setdefault(pair, {})[sender] = (rank, request.topic)

    def priority(item):
        pair, asks = item
        a, b = sorted(pair)
        return (delegation_of[a] == delegation_of[b], len(asks) < 2, sum(rank for rank, _ in asks.values()), a, b)

    busy: set[str] = set()
    pairs = []
    for pair, asks in sorted(requested.items(), key=priority):
        if busy & pair:
            continue
        busy |= pair
        # Whoever wanted this meeting most opens it, on its own topic.
        opener = min(asks, key=lambda name: (asks[name][0], name))
        (partner,) = pair - {opener}
        pairs.append((opener, partner, asks[opener][1], len(asks) == 2))
    return pairs


class Bilaterals:
    def __init__(self, hub: MeetingHub, store: EventStore, *, round_number: int, slots: int, message_limit: int):
        if slots < 1 or message_limit < 2:
            raise ValueError("Bilaterals need at least one slot and two messages")
        self.hub, self.store = hub, store
        self.round, self.slots, self.message_limit = round_number, slots, message_limit

    def _events(self, kind: str, slot: int | None = None):
        return [e for e in self.store.events if e.kind == kind and e.round == self.round
                and (slot is None or e.payload.get("slot") == slot)]

    def _meetings(self, slot: int | None = None) -> list[Meeting]:
        return [m for m in self.hub.meetings.values() if m.round == self.round and (slot is None or m.slot == slot)]

    def _meeting_of(self, name: str) -> Meeting:
        return next(m for m in self._meetings(self.slot) if name in m.participants)

    def _nominations(self) -> dict[str, list[PartnerRequest]]:
        return {e.actor: [PartnerRequest.model_validate(r) for r in e.payload["requests"]]
                for e in self._events("bilateral_nominations", self.slot)}

    def _reported(self) -> set[str]:
        return {e.actor for e in self._events("team_meeting_report", self.slot)}

    @property
    def slot(self) -> int:
        return len(self._events("bilateral_slot_finished")) + 1

    @property
    def done(self) -> bool:
        return self.slot > self.slots

    @property
    def paired(self) -> bool:
        return bool(self._events("bilateral_pairing", self.slot))

    @staticmethod
    def next_speaker(meeting: Meeting) -> str:
        if not meeting.messages:
            return meeting.initiator
        return meeting.partner if meeting.messages[-1]["actor"] == meeting.initiator else meeting.initiator

    def due(self) -> list[tuple[str, str]]:
        """(stage, member) for everyone who can act now."""
        if self.done:
            return []
        if not self.paired:
            nominated = self._nominations()
            return [("nominate", name) for name in self.hub.participants if name not in nominated]
        reported, due = self._reported(), []
        meetings = self._meetings(self.slot)
        for meeting in meetings:
            if meeting.status == "active":
                due.append(("bilateral", self.next_speaker(meeting)))
            else:
                due += [("debrief", name) for name in meeting.participants if name not in reported]
        if not due and meetings and not self._events("bilateral_slot_summary", self.slot):
            due.append((SUMMARY_STAGE, RAPPORTEUR))
        return due

    def context(self, name: str, stage: str) -> dict:
        if stage == SUMMARY_STAGE:
            reports = {}
            for event in self._events("team_meeting_report", self.slot):
                reports.setdefault(event.payload["meeting_id"], {})[event.actor] = {
                    k: event.payload[k] for k in ("summary", "agreements", "next_steps")}
            pairing = self._events("bilateral_pairing", self.slot)[-1].payload
            return {"stage": stage, "round": self.round, "slot": self.slot, "unmatched": pairing["unmatched"],
                    "requests": {actor: [r.model_dump(mode="json") for r in requests]
                                 for actor, requests in self._nominations().items()},
                    "meetings": [{"opened_by": m.initiator, "partner": m.partner, "topic": m.topic, "mutual": m.mutual,
                                  "close_reason": m.close_reason, "closed_by": m.closed_by,
                                  "messages": [{"from": x["actor"], "text": x["text"]} for x in m.messages],
                                  "reports_to_own_team": reports.get(m.id, {})} for m in self._meetings(self.slot)]}
        if stage == "nominate":
            met = {p for m in self._meetings() if name in m.participants for p in m.participants} - {name}
            return {"bilateral_slot": self.slot, "bilateral_slots": self.slots, "already_met": sorted(met),
                    "directory": [{"id": p.id, "delegation": p.delegation, "role": p.role}
                                  for p in self.hub.participants.values() if p.id != name]}
        meeting = self._meeting_of(name)
        other = meeting.partner if name == meeting.initiator else meeting.initiator
        partner = self.hub.participants[other]
        asked = {actor: [r.member for r in requests] for actor, requests in self._nominations().items()}
        return {"bilateral": {
            "meeting_id": meeting.id, "slot": meeting.slot, "topic": meeting.topic,
            "participants": meeting.participants, "opened_by": meeting.initiator,
            "partner": {"id": partner.id, "delegation": partner.delegation, "role": partner.role},
            "you_asked_for_this_meeting": other in asked.get(name, []),
            "partner_asked_for_this_meeting": name in asked.get(other, []),
            "messages": [{"from": m["actor"], "text": m["text"]} for m in meeting.messages],
            "message_limit": self.message_limit, "messages_left": self.message_limit - len(meeting.messages),
            "status": meeting.status, "close_reason": meeting.close_reason, "closed_by": meeting.closed_by}}

    def resolve(self, stage: str, actions: dict[str, MemberAction]):
        for name, action in sorted(actions.items()):
            if stage == "nominate":
                if name in self._nominations():
                    raise ValueError("Partners were already chosen for this slot")
                if any(r.member == name or r.member not in self.hub.participants for r in action.partner_requests):
                    raise ValueError("Partner requests must name other registered members")
                self.store.append("bilateral_nominations", {"slot": self.slot,
                                  "requests": [r.model_dump(mode="json") for r in action.partner_requests]},
                                  round=self.round, visibility="individual", owners=[name], actor=name)
            elif stage == "bilateral":
                meeting = self._meeting_of(name)
                if meeting.status != "active" or self.next_speaker(meeting) != name:
                    raise ValueError("It is not this member's turn in a bilateral")
                self.hub.message(name, meeting.id, action.speech)
                if action.end_conversation:
                    self.hub.close(meeting.id, actor=name, reason="ended")
                elif len(meeting.messages) >= self.message_limit:
                    self.hub.close(meeting.id, reason="limit")
            elif stage == SUMMARY_STAGE:
                # Researcher-only: a neutral account of the whole slot, never fed back to agents.
                self.store.append("bilateral_slot_summary", {"slot": self.slot, "summary": action.speech},
                                  round=self.round, visibility="individual", owners=["researcher"], actor=name)
            elif stage == "debrief":
                if name in self._reported():
                    raise ValueError("This member already reported this slot's bilateral")
                self.hub.report(name, self._meeting_of(name).id, action.meeting_report)
            else:
                raise ValueError(f"Unknown bilateral stage {stage}")
        if stage == "nominate" and not self.due():
            self._pair()
        self._finish_slot()

    def _pair(self):
        nominations = self._nominations()
        met = {frozenset(m.participants) for m in self._meetings()}
        delegation_of = {p.id: p.delegation for p in self.hub.participants.values()}
        meetings = [self.hub.open(opener, partner, topic, round_number=self.round, slot=self.slot, mutual=mutual)
                    for opener, partner, topic, mutual in pair_requests(nominations, delegation_of, met)]
        unmatched = sorted(set(nominations) - {p for m in meetings for p in m.participants})
        # Who meets whom is private: only the researcher sees the whole pairing.
        self.store.append("bilateral_pairing", {"slot": self.slot, "meetings": [m.id for m in meetings],
                          "unmatched": unmatched}, round=self.round, visibility="individual", owners=["researcher"])
        for name in unmatched:
            if nominations[name]:
                self.store.append("bilateral_unmatched", {"slot": self.slot, "requested": [r.member for r in nominations[name]]},
                                  round=self.round, visibility="individual", owners=[name], actor=name)

    def _finish_slot(self):
        if self.done or not self.paired:
            return
        meetings = self._meetings(self.slot)
        if (all(m.status == "closed" for m in meetings) and {p for m in meetings for p in m.participants} <= self._reported()
                and (not meetings or self._events("bilateral_slot_summary", self.slot))):
            self.store.append("bilateral_slot_finished", {"slot": self.slot, "meetings": len(meetings)},
                              round=self.round, visibility="individual", owners=["researcher"])
