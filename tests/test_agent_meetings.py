import pytest

from climate_sim.agents import OfflineBackend
from climate_sim.lab import Lab
from climate_sim.models import MeetingAction, MemberAction, SimulationConfig
from climate_sim.simulation import Simulation


@pytest.fixture(scope="module")
def completed():
    sim = Simulation(SimulationConfig(seed=42))
    sim.run()
    return sim


def test_agents_meet_across_delegations_under_the_protocol(completed):
    meetings = completed.hub.meetings
    assert len(meetings) == 7
    assert sorted(m.status for m in meetings.values()) == ["closed"] * 6 + ["expired"]
    expired = next(m for m in meetings.values() if m.status == "expired")
    assert expired.acknowledgements == {"usa.economic_minister": "pending"}
    expiry = next(e for e in completed.store.events if e.kind == "meeting_expired")
    assert expiry.payload["tick"] == expired.created_tick + 5
    for meeting in meetings.values():
        if meeting.status == "closed":
            assert len({p.split(".")[0] for p in meeting.participants}) == 2 and len(meeting.messages) == 1
    assert all(e.round == 2 for e in completed.store.events if e.kind.startswith("meeting_"))


def test_meeting_records_reach_only_their_exact_participants(completed):
    events = [e for e in completed.store.events if e.kind.startswith("meeting_")]
    assert all(e.visibility == "individual" and e.owners == [e.payload["meeting"]["sender"], *e.payload["meeting"]["invitees"]]
               for e in events)
    for member in completed.agents.values():
        seen = {e.id for e in events if e.id in member.observed_event_ids}
        assert all(member.name in e.owners for e in events if e.id in seen)
    # Invited China's climate adviser, never its head negotiator.
    assert not {e.id for e in events}.intersection(completed.agents["china.head_negotiator"].observed_event_ids)


def test_only_members_with_meeting_business_act_after_the_first_tick(completed):
    scenes = [e for e in completed.store.events if e.kind == "scene_started" and e.payload["stage"] == "meet"]
    assert [len(e.payload["participants"]) for e in scenes] == [18, 7, 7, 7, 1]
    assert all(e.visibility == "individual" and e.owners == ["researcher"] for e in scenes)


def test_colliding_moves_are_rejected_privately_and_the_run_continues():
    class Colliding(OfflineBackend):
        def decide(self, member, context):
            action = super().decide(member, context)
            if context["stage"] == "meet" and context["phase_tick"] == 2:
                # China's adviser closes its active meeting while India's adviser writes to it, in the same tick.
                for meeting in context["meetings"]:
                    if meeting["id"] == "meeting-0001" and member.name == "india.climate_adviser":
                        action.meeting_actions.append(MeetingAction(operation="message", meeting_id="meeting-0001", text="Wait!"))
                    if meeting["id"] == "meeting-0001" and member.name == "china.climate_adviser":
                        action.meeting_actions = [MeetingAction(operation="close", meeting_id="meeting-0001")]
            return action
    sim = Simulation(SimulationConfig(seed=42), backend_factory=lambda name: Colliding())
    assert sim.run()["state"]["status"] == "complete"
    rejected = [e for e in sim.store.events if e.kind == "meeting_action_rejected"]
    assert len(rejected) == 1 and rejected[0].owners == ["india.climate_adviser"]
    assert "active" in rejected[0].payload["error"]


def test_meeting_actions_are_validated_against_the_members_own_view():
    sim = Simulation(SimulationConfig(seed=42))
    member = sim.agents["eu.economic_minister"]
    context = {"stage": "meet", "meetings": [], "directory": [{"id": "china.head_negotiator"}]}
    with pytest.raises(ValueError, match="directory"):
        member.validate_action(MemberAction(meeting_actions=[MeetingAction(operation="request", invitees=["nobody"], topic="x")]), context)
    with pytest.raises(ValueError, match="Unknown meeting"):
        member.validate_action(MemberAction(meeting_actions=[MeetingAction(operation="respond", meeting_id="meeting-0009", accepted=True)]), context)
    with pytest.raises(ValueError, match="only in meeting scenes"):
        member.validate_action(MemberAction(commitment=member.brief.experimental_baseline,
                                            meeting_actions=[MeetingAction(operation="close", meeting_id="m")]),
                               {**context, "stage": "internal"})


def test_lab_replay_scopes_recorded_meetings(tmp_path):
    output = tmp_path / "run"
    Simulation(SimulationConfig(seed=42), output=output).run()
    lab = Lab(output)
    visible = lambda scope: {m["id"] for m in lab.replay(scope)["rounds"]["2"]["meetings"]}
    assert len(visible("researcher")) == 7
    assert visible("public") == set()
    assert visible("china.climate_adviser") == {"meeting-0001", "meeting-0002"}
    assert visible("china.head_negotiator") == set()
    assert lab.hub.meetings == {}  # the manual sandbox stays separate from the saved run


def test_meetings_can_be_turned_off():
    sim = Simulation(SimulationConfig(seed=42, meeting_ticks=0))
    sim.run()
    assert not sim.hub.meetings and not any(e.kind.startswith("meeting") for e in sim.store.events)
