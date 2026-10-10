"""Continuous live bilateral allocation, with ranked requests and no round-wide barriers."""
from __future__ import annotations

import time
from .bilaterals import Bilaterals, pair_requests
from .models import PartnerRequest


class AgentAllocator(Bilaterals):
    max_partners = 5
    wait_seconds = 10

    def __init__(self, hub, store, *, round_number=2, message_limit=8, clock=time.monotonic):
        super().__init__(hub, store, round_number=round_number, slots=1, message_limit=message_limit)
        self.clock = clock
        self.inflight: set[str] = set()
        self.waiting_since: dict[str, float] = {}
        self.reset_waits()

    @property
    def slot(self):
        # Compatibility with archived meeting records; never a scheduling barrier.
        return 1

    def _nominations(self):
        plans = {}
        for e in self.store.events:
            if e.round != self.round:
                continue
            if e.kind == 'agent_partner_plan':
                plans[e.actor] = [PartnerRequest.model_validate(r) for r in e.payload['requests']]
            elif e.kind == 'agent_plan_reset':
                plans.pop(e.actor, None)
        return plans

    def partners(self, name):
        return {p for m in self._meetings() if name in m.participants for p in m.participants} - {name}

    def pending_report(self, name):
        reported = {e.payload['meeting_id'] for e in self._events('team_meeting_report') if e.actor == name}
        return next((m for m in self._meetings() if name in m.participants
                     and m.status == 'closed' and m.id not in reported), None)

    def active_meeting(self, name):
        return next((m for m in self._meetings() if name in m.participants and m.status == 'active'), None)

    def _meeting_of(self, name):
        return self.active_meeting(name) or self.pending_report(name)

    def reset_waits(self):
        self.waiting_since = {name: self.clock() for name in self._nominations()}

    def available(self, name):
        return (len(self.partners(name)) < self.max_partners and not self.active_meeting(name)
                and not self.pending_report(name) and name not in self.inflight)

    def _candidates(self, name):
        return [p for p in self.hub.participants if p != name and p not in self.partners(name)
                and len(self.partners(p)) < self.max_partners]

    def allocate(self):
        plans = self._nominations()
        free = {name for name in plans if self.available(name) and plans[name]}
        # Don't reserve an agent while it is revising its plan or reporting a previous meeting.
        requests = {name: [r for r in plans[name] if r.member in free] for name in free}
        met = {frozenset(m.participants) for m in self._meetings()}
        groups = {p.id: p.delegation for p in self.hub.participants.values()}
        for opener, partner, topic, mutual in pair_requests(requests, groups, met):
            self.hub.open(opener, partner, topic, round_number=self.round, slot=1, mutual=mutual)
            self.waiting_since.pop(opener, None)
            self.waiting_since.pop(partner, None)

    @property
    def done(self):
        plans = self._nominations()
        return not self.inflight and all(not self.active_meeting(name) and not self.pending_report(name)
            and (len(self.partners(name)) >= self.max_partners or not self._candidates(name)
                 or (name in plans and not plans[name])) for name in self.hub.participants)

    def due(self):
        self.allocate()
        now, plans, due = self.clock(), self._nominations(), []
        for name in self.hub.participants:
            if name in self.inflight:
                continue
            meeting = self.active_meeting(name)
            if meeting:
                if self.next_speaker(meeting) == name:
                    due.append(('bilateral', name))
            elif self.pending_report(name):
                due.append(('debrief', name))
            elif len(self.partners(name)) < self.max_partners and self._candidates(name):
                if name not in plans:
                    due.append(('nominate', name))
                elif plans[name]:
                    since = self.waiting_since.setdefault(name, now)
                    if now - since >= self.wait_seconds:
                        due.append(('nominate', name))
        return due

    def snapshot(self):
        plans, now = self._nominations(), self.clock()
        result = []
        for name in self.hub.participants:
            meeting = self.active_meeting(name)
            reporting = self.pending_report(name)
            count = len(self.partners(name))
            state = ('busy' if meeting else 'updating' if reporting else 'planning' if name in self.inflight
                     else 'finished' if count >= self.max_partners or not self._candidates(name)
                     or (name in plans and not plans[name]) else 'free')
            result.append({'agent': name, 'state': state, 'meeting_id': meeting.id if meeting else None,
                           'partners_met': count, 'max_partners': self.max_partners,
                           'waiting_seconds': min(self.wait_seconds, round(max(0, now-self.waiting_since.get(name, now)), 1)),
                           'priorities': [r.model_dump(mode='json') for r in plans.get(name, [])]})
        return result

    def context(self, name, stage):
        if stage == 'nominate':
            return {'allocator_mode': True, 'already_met': sorted(self.partners(name)),
                    'max_partners': self.max_partners, 'remaining_partners': self.max_partners-len(self.partners(name)),
                    'queue_timeout_seconds': self.wait_seconds,
                    'previous_requests': [r.model_dump(mode='json') for r in self._nominations().get(name, [])],
                    'directory': [{'id': p.id, 'delegation': p.delegation, 'role': p.role,
                                   'availability': next(x['state'] for x in self.snapshot() if x['agent'] == p.id)}
                                  for p in self.hub.participants.values() if p.id in self._candidates(name)],
                    'availability': [{k:v for k,v in x.items() if k not in {'priorities','meeting_id'}} for x in self.snapshot()]}
        return super().context(name, stage)

    def resolve(self, stage, actions):
        for name, action in actions.items():
            if stage == 'nominate':
                if not self.available_for_plan(name):
                    raise ValueError('An occupied agent cannot change its partner plan')
                ids = [r.member for r in action.partner_requests]
                if len(set(ids)) != len(ids) or set(ids) - (set(self.hub.participants)-{name}-self.partners(name)):
                    raise ValueError('Choose distinct eligible partners who have not already met you')
                self.store.append('agent_partner_plan', {'requests':[r.model_dump(mode='json') for r in action.partner_requests]},
                                  round=self.round, visibility='individual', owners=[name], actor=name)
                self.waiting_since[name] = self.clock()
            elif stage == 'bilateral':
                meeting = self.active_meeting(name)
                if not meeting or self.next_speaker(meeting) != name:
                    raise ValueError('Not your turn in an active conversation')
                self.hub.message(name, meeting.id, action.speech)
                if action.end_conversation:
                    self.hub.close(meeting.id, actor=name, reason='ended')
                elif len(meeting.messages) >= self.message_limit:
                    self.hub.close(meeting.id, reason='limit')
            elif stage == 'debrief':
                meeting = self.pending_report(name)
                if not meeting:
                    raise ValueError('No conversation awaits your report')
                self.hub.report(name, meeting.id, action.meeting_report)
                # Fresh planning call after every report, with updated team knowledge and availability.
                self.store.append('agent_plan_reset', {}, round=self.round,
                                  visibility='individual', owners=[name], actor=name)
                self.waiting_since.pop(name, None)
            else:
                raise ValueError('Unknown allocator stage')

    def available_for_plan(self, name):
        return not self.active_meeting(name) and not self.pending_report(name) and len(self.partners(name)) < self.max_partners
