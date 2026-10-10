import json

import pytest

from climate_sim.lab import Lab
from climate_sim.models import SimulationConfig
from climate_sim.simulation import Simulation


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    run = tmp_path_factory.mktemp("round-one") / "run"
    Simulation(SimulationConfig(seed=42, team_size=5), output=run).run()
    return Lab(run)


def test_round_one_contains_actual_rosters_and_only_first_round_discussions(lab):
    data = lab.round_one("researcher")
    assert data["round"] == 1 and len(data["countries"]) == 6
    for country in data["countries"]:
        assert len(country["members"]) == 5
        assert len(country["discussion"]) == 10
        assert all(e["round"] == 1 and e["kind"] == "internal_speech"
                   and e["owners"] == [country["id"]] for e in country["discussion"])
    assert "rounds" not in data


def test_agent_details_have_only_that_members_first_round_traces(lab):
    agent_id = "china.climate_adviser"
    data = lab.round_one_agent(agent_id, "researcher")
    assert data["agent"]["private_strategy"]
    assert data["traces"]
    assert all(t["actor"] == agent_id and (t["round"] == 1 or t["payload"]["stage"] in ("nominate", "bilateral", "debrief")) for t in data["traces"])
    assert all(t["payload"]["stage"] in {"prepare", "internal", "mandate", "vote", "announce", "nominate", "bilateral", "debrief"}
               for t in data["traces"])
    assert all(t["payload"]["decision_summary"] for t in data["traces"])
    assert all(e["round"] <= 1 for t in data["traces"] if t["round"] == 1 for e in t["payload"]["observations"])


def test_public_and_other_members_cannot_read_an_agents_private_notes(lab):
    agent_id = "china.climate_adviser"
    for scope in ("public", "india.climate_adviser"):
        data = lab.round_one_agent(agent_id, scope)
        assert "private_strategy" not in data["agent"] and data["traces"] == []
    public = lab.round_one("public")
    assert all(not c["discussion"] for c in public["countries"])
    assert "Prioritize" not in json.dumps(public)
    assert lab.round_one_agent(agent_id, agent_id)["traces"]


def test_older_runs_without_trace_records_keep_preparation_without_inventing_summaries(lab):
    older = Lab.__new__(Lab)
    older.__dict__.update(lab.__dict__)
    older.events = [e for e in lab.events if e.kind != "agent_trace"]
    detail = older.round_one_agent("china.climate_adviser", "researcher")
    assert detail["agent"]["private_strategy"] and detail["traces"] == []
