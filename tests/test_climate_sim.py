import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from climate_sim.agents import DelegationMember, GeminiBackend, OfflineBackend
from climate_sim.briefs import load_briefs
from climate_sim.evaluator import MockClimateEvaluator, ReviewedOutcomeEvaluator, commitment_digest
from climate_sim.memory import EventStore
from climate_sim.models import ClimateCommitment, NegotiationOffer, Role, SimulationConfig
from climate_sim.simulation import Simulation
from climate_sim.voting import ranked_choice


@pytest.fixture(scope="module")
def completed():
    sim = Simulation(SimulationConfig(seed=42))
    result = sim.run()
    return sim, result


def test_official_briefs_have_sources_and_separate_experimental_parameters():
    briefs = load_briefs()
    assert len(briefs) == 6
    for key, brief in briefs.items():
        assert brief.delegation.value == key
        assert len(brief.source_pages) == 2
        assert len(brief.source_sha256) == 64
        assert "NOT official" in brief.adaptation_notes
        assert "Climate Interactive" in brief.attribution
        assert "MIT Sloan" in brief.attribution
        assert "UMass" in brief.attribution
    alliance = load_briefs(us_variant="climate_alliance_proxy")["usa"]
    assert "NO official negotiating standing" in alliance.adaptation_notes


@pytest.mark.parametrize("changes", [
    {"reduction_start_year": 2026, "emissions_peak_year": 2030},
    {"annual_reduction_rate_percent": -1}, {"afforestation_percent": 101},
    {"climate_fund_billion_per_year": float("nan")},
    {"deforestation_prevention_percent": float("inf")},
    {"emissions_peak_year": 2030.5}, {"unexpected": True},
])
def test_invalid_commitments_rejected(changes):
    with pytest.raises(ValidationError):
        load_briefs()["china"].experimental_baseline.changed(changes)


def test_private_views_and_copies_do_not_leak():
    store = EventStore()
    store.append("test", {"secret": "CHINA_PRIVATE_SENTINEL"}, round=1, visibility="delegation", owners=["china"])
    store.append("test", {"secret": "INDIVIDUAL_PRIVATE_SENTINEL"}, round=1, visibility="individual", owners=["china.climate_adviser"])
    store.append("test", {"offer": "PRIVATE_OFFER_SENTINEL"}, round=1, visibility="delegation", owners=["china", "eu"])
    store.append("test", {"speech": "public"}, round=1, visibility="public")
    outsider = json.dumps(store.view("india.head_negotiator", "india", 100))
    assert "SENTINEL" not in outsider
    head_view = json.dumps(store.view("china.head_negotiator", "china", 100))
    assert "CHINA_PRIVATE_SENTINEL" in head_view and "INDIVIDUAL_PRIVATE_SENTINEL" not in head_view
    eu_view = json.dumps(store.view("eu.head_negotiator", "eu", 100))
    assert "PRIVATE_OFFER_SENTINEL" in eu_view and "CHINA_PRIVATE_SENTINEL" not in eu_view
    copy = store.view("china.head_negotiator", "china", 100)
    copy[0]["payload"]["secret"] = "mutated"
    assert store.events[0].payload["secret"] == "CHINA_PRIVATE_SENTINEL"


def test_entity_rejects_unauthorized_observation():
    member = DelegationMember("india.head_negotiator", load_briefs()["india"], Role.HEAD, SimulationConfig(seed=1), OfflineBackend())
    with pytest.raises(PermissionError):
        member.observe(json.dumps({"events": [{"visibility": "delegation", "owners": ["china"], "id": "secret"}]}))


def test_all_nine_phases_and_two_team_meetings(completed):
    sim, result = completed
    assert len(sim.agents) == 18
    assert all(len(team) == 3 for team in sim.teams.values())
    assert len(sim.heads) == 6
    assert set(sim.commitments) == {1, 2}
    assert all(len(cs) == 6 for cs in sim.commitments.values())
    scenes = [e for e in sim.store.events if e.kind == "scene_started"]
    internals = [e for e in scenes if e.payload["stage"] == "internal"]
    assert len(internals) == 12
    assert all(len(e.payload["participants"]) == 3 and len(e.owners) == 1 for e in internals)
    summits = [e for e in scenes if e.payload["stage"] in ("negotiate", "announce", "submit")]
    assert all(set(e.payload["participants"]) == {head.name for head in sim.heads} for e in summits)
    assert result["state"]["status"] == "complete"
    assert sim.outcomes[2].warming_celsius < sim.outcomes[1].warming_celsius


def test_every_actual_observation_obeys_scope(completed):
    sim, _ = completed
    events = {e.id: e for e in sim.store.events}
    for member in sim.agents.values():
        assert all(events[key].visible_to(member.name, member.brief.delegation.value) for key in member.observed_event_ids)
        foreign_internal = {e.id for e in sim.store.events if e.kind == "internal_speech" and e.owners != [member.brief.delegation.value]}
        assert not foreign_internal.intersection(member.observed_event_ids)


def test_simultaneous_submission_no_same_round_peeking(completed):
    sim, _ = completed
    submission = next(e for e in sim.store.events if e.kind == "commitments" and e.round == 2)
    # Final barrier publishes only after all six actors have acted.
    assert all(submission.id not in head.observed_event_ids for head in sim.heads)
    assert submission.payload["simultaneous"]


def test_offline_seed_replay_is_exact(completed):
    sim, result = completed
    replay = Simulation(SimulationConfig(seed=42))
    assert replay.run() == result
    assert [e.model_dump() for e in replay.store.events] == [e.model_dump() for e in sim.store.events]


def test_voting_and_larger_homogeneous_team():
    sim = Simulation(SimulationConfig(seed=7, team_size=4, decision_rule="vote", composition="homogeneous", offer_visibility="private"))
    result = sim.run()
    assert len(sim.agents) == 24
    assert len(sim.heads) == 6
    assert all(not offer.public for offer in sim.offers.values())
    assert all(len(m.internal_support) == 4 for m in sim.mandates.values())
    mandates = [e for e in sim.store.events if e.kind == "national_mandate"]
    assert all(e.payload["election_rounds"] for e in mandates)
    assert result["state"]["metrics"]["global"]["is_mock"]


def test_ranked_choice_transfers_votes_and_declares_ties():
    winner, trace = ranked_choice({"a": ["x", "y", "z"], "b": ["y", "x", "z"], "c": ["z", "y", "x"]}, ["x", "y", "z"])
    assert winner == "y"
    assert trace[0]["eliminated"] == "z"
    with pytest.raises(ValueError):
        ranked_choice({"a": ["x", "x"]}, ["x", "y"])


def test_mock_monotonicity_and_finance_separation():
    evaluator = MockClimateEvaluator()
    baseline = {k: b.experimental_baseline for k, b in load_briefs().items()}
    stronger = {k: c.changed({"emissions_peak_year": 2026, "reduction_start_year": 2026,
                            "annual_reduction_rate_percent": 8, "deforestation_prevention_percent": 100, "afforestation_percent": 100}) for k, c in baseline.items()}
    first, last = evaluator.evaluate(baseline), evaluator.evaluate(stronger)
    assert first.is_mock and last.is_mock and last.warming_celsius < first.warming_celsius
    finance_only = {k: c.changed({"climate_fund_billion_per_year": 10}) for k, c in baseline.items()}
    assert evaluator.evaluate(finance_only) == first
    with pytest.raises(ValueError):
        evaluator.evaluate({"china": baseline["china"]})


def test_offer_validations_and_conditions():
    offer = NegotiationOffer(id="test", sender="china", recipients=["eu"], offered_changes={"emissions_peak_year": 2028}, conditions={"eu_climate_fund_contribution_minimum": 40}, expires_after_round=2)
    world = {k: b.experimental_baseline for k, b in load_briefs().items()}
    assert offer.conditions_met(world)
    world["eu"] = world["eu"].changed({"climate_fund_billion_per_year": 20})
    assert not offer.conditions_met(world)
    with pytest.raises(ValueError):
        NegotiationOffer(id="bad", sender="china", recipients=["china"], offered_changes={"annual_reduction_rate_percent": 2}, expires_after_round=1)
    with pytest.raises(ValueError):
        NegotiationOffer(id="bad", sender="china", recipients=["eu"], offered_changes={"delegation": 2}, expires_after_round=1)


def test_public_export_excludes_private_data_and_artifacts_reopen(tmp_path):
    output = tmp_path / "run"
    sim = Simulation(SimulationConfig(seed=11), output=output)
    result = sim.run()
    state = json.loads((output / "state.json").read_text())
    assert state == result["state"]
    public = json.loads((output / "public.json").read_text())
    assert all(e["visibility"] == "public" for e in public["events"])
    assert not {"individual_preparation", "internal_speech", "national_mandate", "internal_ballot"}.intersection(e["kind"] for e in public["events"])
    for event in public["events"]:
        if event["kind"] == "negotiation_offer":
            assert event["payload"]["offer"]["public"]
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    assert len(events) == len(sim.store.events)
    assert "DETERMINISTIC MOCK" in (output / "report.md").read_text()
    assert (output.stat().st_mode & 0o777) == 0o700
    assert (output / "state.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        Simulation(SimulationConfig(seed=11), output=output)


def test_reviewed_bridge_refuses_unknown_or_tampered_inputs(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text("[]")
    world = {k: b.experimental_baseline for k, b in load_briefs().items()}
    with pytest.raises(ValueError, match="No reviewed"):
        ReviewedOutcomeEvaluator(path).evaluate(world)
    outcome = MockClimateEvaluator().evaluate(world).model_dump(mode="json")
    outcome.update(is_mock=False, evaluator="c-roads-reviewed-test", explanation="Test fixture, not actual C-ROADS output")
    record = {"commitments": {k: c.model_dump(mode="json") for k, c in world.items()},
              "commitment_sha256": commitment_digest(world), "outcome": outcome,
              "simulator_version": "fixture", "scenario_reference": "fixture-only"}
    path.write_text(json.dumps([record]))
    assert not ReviewedOutcomeEvaluator(path).evaluate(world).is_mock
    record["commitment_sha256"] = "tampered"
    path.write_text(json.dumps([record]))
    with pytest.raises(ValueError, match="digest"):
        ReviewedOutcomeEvaluator(path).evaluate(world)


def test_failed_backend_leaves_partial_audit(tmp_path):
    class BrokenBackend:
        label = "broken-test"
        def decide(self, member, context):
            raise RuntimeError("failure without credential values")
    output = tmp_path / "broken"
    sim = Simulation(SimulationConfig(seed=1), output=output, backend_factory=lambda name: BrokenBackend())
    with pytest.raises(RuntimeError):
        sim.run()
    assert json.loads((output / "failure.json").read_text())["status"] == "failed"
    assert json.loads((output / "events.jsonl").read_text().splitlines()[-1])["kind"] == "run_failed"


def test_minimal_memory_preserves_only_own_live_team_state():
    captured = []
    class RecordingBackend(OfflineBackend):
        def decide(self, member, context):
            captured.append((member.name, json.loads(json.dumps(context))))
            return super().decide(member, context)
    sim = Simulation(SimulationConfig(seed=42, memory_length=1, rounds=3),
                     backend_factory=lambda name: RecordingBackend())
    assert sim.run()["state"]["status"] == "complete"
    for name, context in captured:
        assert len(context["events"]) <= 1
        assert all(actor.startswith(name.split(".")[0] + ".") for actor in context["shared_positions"])
        assert all(raw["delegation"] == name.split(".")[0] for raw in context["shared_positions"].values())


def test_reviewed_missing_outcome_preserves_handoff_inputs(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text("[]")
    output = tmp_path / "handoff"
    sim = Simulation(SimulationConfig(seed=42), output=output, evaluator=ReviewedOutcomeEvaluator(path))
    with pytest.raises(ValueError, match="No reviewed"):
        sim.run()
    record = json.loads((output / "round-01-commitments.json").read_text())
    commitments = {k: ClimateCommitment.model_validate(raw) for k, raw in record["commitments"].items()}
    assert len(commitments) == 6 and commitment_digest(commitments) == record["commitment_sha256"]
    assert not (output / "state.json").exists()
    assert (output / "failure.json").exists()


def test_gemini_repairs_phase_errors_without_foreign_context():
    from types import SimpleNamespace
    requests = []
    class FakeModels:
        def generate_content(self, **kwargs):
            requests.append(kwargs)
            # Schema-valid but phase-invalid first attempt: preparation requires a commitment.
            text = "{}" if len(requests) == 1 else json.dumps({
                "commitment": load_briefs()["india"].experimental_baseline.model_dump(mode="json"),
                "private_strategy": "OWN_STRATEGY"})
            return SimpleNamespace(text=text)
    backend = GeminiBackend.__new__(GeminiBackend)
    backend.model, backend.label = "test-model", "gemini:test-model"
    backend.client = SimpleNamespace(models=FakeModels())
    sim = Simulation(SimulationConfig(seed=42))
    member = sim.agents["india.head_negotiator"]
    sim.store.append("test", {"secret": "FOREIGN_PRIVATE_SENTINEL"}, round=1,
                     visibility="delegation", owners=["china"])
    member.observe(json.dumps(sim.context(member)))
    action = backend.decide(member, member._context)
    assert action.commitment.delegation.value == "india" and len(requests) == 2
    assert "requires a structured commitment" in requests[1]["contents"]
    assert all("FOREIGN_PRIVATE_SENTINEL" not in request["contents"] for request in requests)
    assert all(request["config"].response_mime_type == "application/json" for request in requests)


def test_backend_error_payload_is_not_logged(caplog, capsys):
    class BrokenBackend:
        label = "broken-test"
        def decide(self, member, context):
            raise RuntimeError("PROVIDER_SECRET_SENTINEL")
    sim = Simulation(SimulationConfig(seed=42), backend_factory=lambda name: BrokenBackend())
    with pytest.raises(RuntimeError):
        sim.run()
    captured = capsys.readouterr()
    assert "PROVIDER_SECRET_SENTINEL" not in captured.out + captured.err + caplog.text


def test_gemini_schema_serializes_through_real_sdk(monkeypatch):
    import httpx
    from google import genai
    from google.genai import types

    monkeypatch.setenv("GEMINI_API_KEY", "test-only-key")
    brief = load_briefs()["india"]
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{
            "text": json.dumps({"commitment": brief.experimental_baseline.model_dump(mode="json"),
                                "private_strategy": "Prefer supported domestic development"})
        }]}, "finishReason": "STOP"}]})

    backend = GeminiBackend("test-model")
    backend.client.close()
    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        with genai.Client(api_key="test-only-key", http_options=types.HttpOptions(httpx_client=transport)) as client:
            backend.client = client
            member = DelegationMember("india.head_negotiator", brief, Role.HEAD,
                                      SimulationConfig(seed=42), backend)
            action = backend.decide(member, {"stage": "prepare", "round": 1, "events": []})

    assert action.commitment == brief.experimental_baseline
    assert len(requests) == 1
    schema = requests[0]["generationConfig"]["responseJsonSchema"]
    assert schema["additionalProperties"] is False
    # Preserve both strict objects and typed dictionaries needed by negotiations.
    offer_schema = schema["$defs"]["NegotiationOffer"]
    assert offer_schema["additionalProperties"] is False
    assert "additionalProperties" in offer_schema["properties"]["offered_changes"]
