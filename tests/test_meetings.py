import json
from pathlib import Path

import pytest

from climate_sim.lab import Lab
from climate_sim.meetings import MeetingHub, Participant
from climate_sim.memory import EventStore
from climate_sim.models import SimulationConfig
from climate_sim.simulation import Simulation


@pytest.fixture
def hub():
    return MeetingHub([
        Participant(id="china.climate_adviser", delegation="china", role="climate_adviser"),
        Participant(id="china.head_negotiator", delegation="china", role="head_negotiator"),
        Participant(id="eu.economic_minister", delegation="eu", role="economic_minister"),
        Participant(id="india.climate_adviser", delegation="india", role="climate_adviser"),
    ])


def test_any_role_can_request_cross_country_group(hub):
    meeting = hub.request("china.climate_adviser", ["eu.economic_minister", "india.climate_adviser"], "Funding and implementation")
    assert meeting.status == "pending" and meeting.expires_tick == 5
    assert hub.respond("eu.economic_minister", meeting.id, True).status == "pending"
    with pytest.raises(ValueError, match="active"):
        hub.message("china.climate_adviser", meeting.id, "Not yet")
    assert hub.respond("india.climate_adviser", meeting.id, True).status == "active"
    assert hub.message("india.climate_adviser", meeting.id, "Can we pool implementation support?").messages[-1]["actor"] == "india.climate_adviser"


def test_invitation_and_messages_do_not_leak_to_uninvited_teammate(hub):
    meeting = hub.request("china.climate_adviser", ["eu.economic_minister"], "PRIVATE_TOPIC_SENTINEL")
    hub.respond("eu.economic_minister", meeting.id, True)
    hub.message("china.climate_adviser", meeting.id, "PRIVATE_MESSAGE_SENTINEL")
    assert hub.view("china.head_negotiator")["meetings"] == []
    assert "PRIVATE_MESSAGE_SENTINEL" in json.dumps(hub.view("eu.economic_minister"))
    assert "SENTINEL" not in json.dumps(hub.store.view("china.head_negotiator", "china", 100))
    for event in hub.store.events:
        assert event.visibility == "individual"
        assert set(event.owners) == {"china.climate_adviser", "eu.economic_minister"}
    with pytest.raises(PermissionError):
        hub.message("china.head_negotiator", meeting.id, "Illegal")
    with pytest.raises(PermissionError):
        hub.respond("india.climate_adviser", meeting.id, True)


def test_exact_five_tick_deadline_and_next_action(hub):
    meeting = hub.request("china.climate_adviser", ["eu.economic_minister", "india.climate_adviser"], "Coalition")
    hub.respond("eu.economic_minister", meeting.id, True)
    hub.advance(4)
    assert hub.meetings[meeting.id].status == "pending"
    hub.advance()
    assert hub.meetings[meeting.id].status == "expired"
    with pytest.raises(ValueError):
        hub.respond("india.climate_adviser", meeting.id, True)
    with pytest.raises(ValueError):
        hub.message("china.climate_adviser", meeting.id, "Too late")
    next_meeting = hub.request("china.climate_adviser", ["china.head_negotiator"], "Find another action")
    assert next_meeting.expires_tick == 10 and next_meeting.status == "pending"
    expiry = next(e for e in hub.store.events if e.kind == "meeting_expired")
    assert expiry.payload["tick"] == 5


def test_ack_at_tick_four_activates_without_later_expiry(hub):
    meeting = hub.request("india.climate_adviser", ["eu.economic_minister"], "Private discussion")
    hub.advance(4)
    hub.respond("eu.economic_minister", meeting.id, True)
    hub.advance(10)
    assert hub.meetings[meeting.id].status == "active"


def test_decline_terminates_request(hub):
    meeting = hub.request("china.head_negotiator", ["eu.economic_minister", "india.climate_adviser"], "Group")
    assert hub.respond("eu.economic_minister", meeting.id, False).status == "declined"
    with pytest.raises(ValueError):
        hub.respond("india.climate_adviser", meeting.id, True)


@pytest.mark.parametrize("invitees", [["china.climate_adviser"], ["eu.economic_minister", "eu.economic_minister"], [], ["unknown"]])
def test_bad_invitees_rejected(hub, invitees):
    with pytest.raises((ValueError, PermissionError)):
        hub.request("china.climate_adviser", invitees, "Test")


def test_views_are_copies_and_repeat_ack_not_allowed(hub):
    meeting = hub.request("china.climate_adviser", ["eu.economic_minister", "india.climate_adviser"], "Discussion")
    meeting.topic = "mutated"
    assert hub.meetings[meeting.id].topic == "Discussion"
    view = hub.view("china.climate_adviser")
    view["meetings"][0]["topic"] = "mutated"
    assert hub.meetings[meeting.id].topic == "Discussion"
    hub.respond("eu.economic_minister", meeting.id, True)
    with pytest.raises(ValueError):
        hub.respond("eu.economic_minister", meeting.id, True)
    with pytest.raises(ValueError):
        hub.advance(True)


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    output = tmp_path_factory.mktemp("agent-lab") / "replay"
    Simulation(SimulationConfig(seed=42), output=output).run()
    return Lab(output)


def test_researcher_can_inspect_round_specific_thoughts_and_discussions(lab):
    data = lab.replay("researcher")
    assert len(data["roster"]) == 18
    assert all(len(d["members"]) == 3 for d in data["delegations"])
    first, second = data["rounds"]["1"]["teams"]["china"], data["rounds"]["2"]["teams"]["china"]
    assert len(first["speeches"]) == len(second["speeches"]) == 6
    assert all(event["round"] == 1 for event in first["speeches"])
    assert all(event["round"] == 2 for event in second["speeches"])
    assert first["disagreement_before"] > first["disagreement_after"]
    assert len(first["pairwise_distance"]) == 3
    assert len(first["mandate"]["mandate"]["internal_support"]) == 3


def test_public_replay_excludes_every_private_field(lab):
    data = lab.replay()
    assert all("private_strategy" not in a and "initial_preference" not in a for a in data["roster"])
    for round_data in data["rounds"].values():
        for team in round_data["teams"].values():
            assert not team["speeches"] and not team["positions"] and not team["starting_positions"]
            assert team["mandate"] is None
        assert all(e["visibility"] == "public" for e in round_data["public_events"])


def test_member_replay_has_own_private_preference_and_only_own_team(lab):
    actor = "china.climate_adviser"
    data = lab.replay(actor)
    assert sum("private_strategy" in a for a in data["roster"]) == 1
    own = next(a for a in data["roster"] if a["agent"] == actor)
    assert own["private_strategy"]
    assert actor in data["rounds"]["1"]["teams"]["china"]["starting_positions"]
    assert len(data["rounds"]["1"]["teams"]["china"]["starting_positions"]) == 1
    for round_data in data["rounds"].values():
        for region, team in round_data["teams"].items():
            if region != "china":
                assert not team["speeches"] and not team["positions"] and team["mandate"] is None


def test_human_controller_uses_same_protocol_and_cannot_impersonate(tmp_path):
    output = tmp_path / "run"
    Simulation(SimulationConfig(seed=42), output=output).run()
    lab = Lab(output)
    actor = "india.climate_adviser"
    lab.act({"operation": "claim", "actor": actor})
    assert lab.human_role == actor and lab.hub.participants[actor].controller == "human"
    existing_meetings = set(lab.hub.meetings)
    lab.act({"operation": "request", "actor": actor, "invitees": ["eu.economic_minister"], "topic": "Human invitation"})
    new_meetings = set(lab.hub.meetings) - existing_meetings
    assert len(new_meetings) == 1
    meeting_id = new_meetings.pop()
    with pytest.raises(PermissionError):
        lab.act({"operation": "respond", "actor": "eu.economic_minister", "meeting_id": meeting_id, "accepted": True})
    lab.act({"operation": "release"})
    assert lab.human_role is None and lab.hub.participants[actor].controller == "agent"
    lab.act({"operation": "respond", "actor": "eu.economic_minister", "meeting_id": meeting_id, "accepted": True})
    assert lab.hub.meetings[meeting_id].status == "active"


def test_meeting_events_persist_with_exact_participant_scope(tmp_path):
    store = EventStore(tmp_path / "meetings.jsonl")
    participants = [Participant(id="a", delegation="china", role="climate_adviser"), Participant(id="b", delegation="eu", role="economic_minister")]
    hub = MeetingHub(participants, store=store)
    meeting = hub.request("a", ["b"], "Test")
    hub.respond("b", meeting.id, True)
    hub.message("a", meeting.id, "Inspect this")
    records = [json.loads(line) for line in (tmp_path / "meetings.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in records] == ["meeting_requested", "meeting_acknowledged", "meeting_message"]
    assert all(e["owners"] == ["a", "b"] and e["visibility"] == "individual" for e in records)
