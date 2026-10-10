from climate_sim.lab import Lab
from climate_sim.models import SimulationConfig
from climate_sim.simulation import Simulation
import pytest


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
