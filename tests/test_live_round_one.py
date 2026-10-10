import json
import threading
from types import SimpleNamespace

import pytest

from climate_sim.agents import GeminiBackend, OfflineBackend
from climate_sim.lab import Lab
from climate_sim.live import LiveLab
from climate_sim.models import SimulationConfig


class RecordingBackend(OfflineBackend):
    def __init__(self, records, entered=None, release=None, fail=False):
        self.records, self.entered, self.release, self.fail = records, entered, release, fail

    def decide(self, member, context):
        self.records.append((member.name, json.loads(json.dumps(context))))
        if context["stage"] == "internal":
            if self.entered:
                self.entered.set()
                assert self.release.wait(5), "test did not release blocked call"
            if self.fail:
                raise ValueError("SECRET_PROVIDER_ERROR")
        action = super().decide(member, context)
        if context["stage"] == "internal":
            action.speech = f"Reply to {context['discussion'][-1]['id'] if context['discussion'] else 'opening'}"
        return action


def wait(lab, predicate):
    with lab.condition:
        assert lab.condition.wait_for(predicate, timeout=5), lab.controls


def test_live_calls_use_latest_messages_and_resume_the_next_speaker(tmp_path):
    records = []
    run = tmp_path / "live"
    lab = LiveLab(SimulationConfig(seed=42, memory_length=1), output=run, message_budget=4,
                  backend_factory=lambda _: RecordingBackend(records))
    try:
        assert records == []  # Merely opening the viewer spends no calls.
        assert lab.round_one("researcher")["mode"] == "live"
        lab.sim.store.append("foreign", {"secret": "FOREIGN_SECRET"}, round=1,
                             visibility="delegation", owners=["china"])
        lab.act({"operation": "start", "country": "india"})
        wait(lab, lambda: lab.controls["india"]["status"] == "limited" and not lab.controls["india"]["thinking_agent"])
        calls = [(name, c) for name, c in records if c["stage"] == "internal"]
        assert len(calls) == 4
        country = next(c for c in lab.round_one("researcher")["countries"] if c["id"] == "india")
        messages = country["discussion"]
        assert len(messages) == 4
        for index, (name, context) in enumerate(calls):
            assert context["discussion_revision"] == index
            assert [e["id"] for e in context["discussion"]] == [e["id"] for e in messages[:index]]
            assert "FOREIGN_SECRET" not in json.dumps(context)
            trace = lab.trace(messages[index]["trace_id"], "researcher")
            assert trace["actor"] == name
            assert trace["payload"]["resolution_event_ids"] == [messages[index]["id"]]
            assert trace["payload"]["inputs"]["discussion_revision"] == index
        lab.act({"operation": "start", "country": "india"})
        wait(lab, lambda: lab.controls["india"]["messages"] == 8 and not lab.controls["india"]["thinking_agent"])
        resumed = [name for name, c in records if c["stage"] == "internal"]
        assert resumed[4] == lab.sim.teams["india"][1].name
        assert len([c for _, c in records if c["stage"] == "prepare"]) == 3
        archived = Lab(run)
        archived_country = next(c for c in archived.round_one("researcher")["countries"] if c["id"] == "india")
        assert len(archived_country["discussion"]) == 8
        assert (run / "state.json").stat().st_mode & 0o777 == 0o600
        assert (run / "events.jsonl").stat().st_mode & 0o777 == 0o600
    finally:
        lab.close()


def test_pause_while_call_is_in_flight_remains_responsive_and_no_duplicate_start():
    records, entered, release = [], threading.Event(), threading.Event()
    lab = LiveLab(SimulationConfig(seed=42), message_budget=3,
                  backend_factory=lambda _: RecordingBackend(records, entered, release))
    try:
        lab.act({"operation": "start", "country": "eu"})
        assert entered.wait(5)
        lab.act({"operation": "start", "country": "eu"})
        assert lab.controls["eu"]["remaining"] == 3
        lab.act({"operation": "pause", "country": "eu"})
        data = lab.round_one("researcher")  # Does not block on the provider.
        assert next(c for c in data["countries"] if c["id"] == "eu")["live"]["status"] == "pausing"
        release.set()
        wait(lab, lambda: lab.controls["eu"]["status"] == "paused" and not lab.controls["eu"]["thinking_agent"])
        assert lab.controls["eu"]["messages"] == 1
        assert len([c for _, c in records if c["stage"] == "internal"]) == 1
    finally:
        release.set()
        lab.close()


def test_failure_stops_loop_without_fabricating_speech_and_scopes_are_preserved(capsys, caplog):
    records = []
    lab = LiveLab(SimulationConfig(seed=42), backend_factory=lambda _: RecordingBackend(records, fail=True))
    try:
        lab.act({"operation": "start", "country": "china"})
        wait(lab, lambda: lab.controls["china"]["status"] == "error" and not lab.controls["china"]["thinking_agent"])
        assert lab.controls["china"]["messages"] == 0
        own = lab.round_one_agent("china.head_negotiator", "china.head_negotiator")
        assert len(own["failures"]) == 1
        for scope in ("public", "india.climate_adviser"):
            hidden = lab.round_one_agent("china.head_negotiator", scope)
            assert hidden["traces"] == hidden["failures"] == []
            assert "private_strategy" not in hidden["agent"]
            assert "live" not in next(c for c in lab.round_one(scope)["countries"] if c["id"] == "china")
        with pytest.raises(ValueError, match="Unknown country"):
            lab.act({"operation": "start", "country": "bad"})
        with pytest.raises(ValueError, match="start or pause"):
            lab.act({"operation": "advance", "country": "china"})
        assert "SECRET_PROVIDER_ERROR" not in capsys.readouterr().out + caplog.text
        assert "SECRET_PROVIDER_ERROR" not in json.dumps(own)
    finally:
        lab.close()


def test_real_sdk_thinking_configuration_usage_and_thought_summary_are_recorded(monkeypatch):
    import httpx
    from google import genai
    from google.genai import types

    monkeypatch.setenv("GEMINI_API_KEY", "TEST_KEY_NEVER_RECORDED")
    requests = []
    lab = LiveLab(SimulationConfig(seed=42))
    member = lab.sim.agents["india.head_negotiator"]
    from climate_sim import agents
    monkeypatch.setitem(agents._UPLOADED_PDFS, member.brief.source_sha256, "https://example.invalid/files/india")
    backend = GeminiBackend("test-model", thinking_level="medium", include_thoughts=True, max_output_tokens=8192)
    backend.client.close()

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"modelVersion": "resolved-model", "responseId": "response-1",
            "usageMetadata": {"promptTokenCount": 123, "candidatesTokenCount": 45,
                              "thoughtsTokenCount": 67, "totalTokenCount": 235},
            "candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
                {"thought": True, "text": "Provider summary for authorized evidence.", "thoughtSignature": "U0lHTkFUVVJF"},
                {"text": json.dumps({"commitment": member.brief.experimental_baseline.model_dump(mode="json"),
                    "private_strategy": "Own preparation", "decision_summary": {"summary": "Preparation evidence",
                    "intended_action": "propose", "decision_type": "propose", "confidence": .8}})}]}}]})

    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            with genai.Client(api_key="TEST_KEY_NEVER_RECORDED", http_options=types.HttpOptions(httpx_client=transport)) as client:
                backend.client = client
                member.backend = backend
                context = lab.sim.context(member)
                context["continuous_discussion"] = True
                member.observe(json.dumps(context))
                member.act()
        config = requests[0]["generationConfig"]
        # google-genai 2.29 passes ThinkingConfig proto field names through unchanged.
        assert config["thinkingConfig"] == {"include_thoughts": True, "thinking_level": "MEDIUM"}
        assert config["maxOutputTokens"] == 8192
        generation = member.action_traces[-1]["generation"]
        call = generation["calls"][0]
        assert call["accepted"] and call["model_version"] == "resolved-model"
        assert call["usage"]["thoughts_token_count"] == 67
        assert call["thought_summaries"] == ["Provider summary for authorized evidence."]
        assert json.loads(call["input"])["name"] == member.name
        assert "TEST_KEY_NEVER_RECORDED" not in json.dumps(generation)
        assert "U0lHTkFUVVJF" not in json.dumps(generation)
    finally:
        lab.close()
