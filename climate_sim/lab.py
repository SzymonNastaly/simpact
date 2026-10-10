"""Local Round 1 viewer with private replay inspection APIs.

Run: python -m climate_sim.lab --port 8766
Not a production multi-user service. The localhost operator is the researcher;
participant views filter data server-side but do not implement user authentication.
Meeting sandbox actions are manual and never pretend to be LLM-generated replies.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from .agents import policy_distance
from .bilaterals import STAGES as BILATERAL_STAGES
from .briefs import load_briefs, load_observers
from .meetings import fold_meetings
from .memory import Event
from .metrics import dispersion
from .models import ClimateCommitment, ObserverId

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend"


class Lab:
    def __init__(self, run: Path):
        self.state = json.loads((run / "state.json").read_text())
        self.events = [Event.model_validate_json(line) for line in (run / "events.jsonl").read_text().splitlines()]
        config = self.state["config"]
        self.briefs = load_briefs(us_variant=config["us_variant"], world=config.get("world", "six"))
        self.observer_briefs = load_observers(config.get("observers", []))
        self.lock = threading.RLock()
        self.run_name = run.name

    def replay(self, scope: str = "public") -> dict:
        researcher = scope == "researcher"
        member = self.state["agents"].get(scope)
        if scope not in ("researcher", "public") and member is None:
            raise PermissionError("Unknown viewer")
        visible = [e for e in self.events if researcher or
                   (e.visible_to(scope, member["delegation"]) if member else e.visibility == "public")]
        roster = []
        for key, agent in self.state["agents"].items():
            item = {field: agent[field] for field in ("agent", "delegation", "role", "personality", "backend")}
            if researcher or scope == key:
                item["private_strategy"] = agent["private_strategy"]
                item["initial_preference"] = agent["initial_preference"]
            roster.append(item)
        rounds = {}
        for round_number in sorted({e.round for e in self.events if e.round > 0 and
                                   e.kind in {"commitments", "individual_preparation", "internal_speech"}}):
            round_events = [e for e in visible if e.round == round_number]
            teams = {}
            for region, brief in self.briefs.items():
                speeches = [e for e in round_events if e.kind == "internal_speech" and e.owners == [region]]
                last_positions = {e.actor: e.payload["commitment"] for e in speeches}
                starting = {}
                for agent in roster:
                    if agent["delegation"] != region:
                        continue
                    if round_number == 1 and "initial_preference" in agent:
                        starting[agent["agent"]] = agent["initial_preference"]
                    else:
                        previous = [e for e in visible if e.kind == "internal_speech" and
                                    e.round < round_number and e.actor == agent["agent"]]
                        if previous:
                            starting[agent["agent"]] = previous[-1].payload["commitment"]
                mandate_events = [e for e in round_events if e.kind == "national_mandate" and e.owners == [region]]
                mandate = mandate_events[-1].payload if mandate_events else None
                # Only researcher knows every member's initial private preference in R1.
                before = dispersion([ClimateCommitment.model_validate(c) for c in starting.values()]) if len(starting) == len(last_positions) and len(starting) > 1 else None
                after = dispersion([ClimateCommitment.model_validate(c) for c in last_positions.values()]) if len(last_positions) > 1 else None
                teams[region] = {"speeches": [e.model_dump(mode="json") for e in speeches],
                    "starting_positions": starting, "positions": last_positions, "mandate": mandate,
                    "disagreement_before": before, "disagreement_after": after,
                    "pairwise_distance": [{"a": a, "b": b, "distance": policy_distance(ClimateCommitment.model_validate(ca), ClimateCommitment.model_validate(cb))}
                        for a, ca in last_positions.items() for b, cb in last_positions.items() if a < b]}
            # Filtered events preserve exact participant scopes for saved conversations.
            rounds[str(round_number)] = {"teams": teams, "meetings": list(fold_meetings(round_events).values()),
                "traces": [self.trace_overview(e) for e in round_events if e.kind == "agent_trace"],
                "timeline": [self.trace_overview(e) if e.kind == "agent_trace" else e.model_dump(mode="json") for e in round_events if e.kind in (
                    "individual_preparation", "internal_speech", "agent_trace", "internal_ballot",
                    "national_mandate", "summit_speech", "bilateral_nominations", "bilateral_unmatched", "meeting_opened",
                    "meeting_message", "meeting_closed", "team_meeting_report", "observer_statement")],
                "notifications": [e.model_dump(mode="json") for e in round_events if e.kind == "team_meeting_report"],
                "public_events": [e.model_dump(mode="json") for e in round_events if e.visibility == "public" and e.kind in ("summit_speech", "observer_statement", "commitments", "negotiation_offer")],
                "climate_outcome": self.state["outcomes_by_round"].get(str(round_number))}
        return {"run": self.run_name, "scope": scope, "config": self.state["config"], "roster": roster,
                "delegations": [{"id": k, "name": b.title, "goals": b.goals,
                                "members": [x["agent"] for x in roster if x["delegation"] == k]} for k, b in self.briefs.items()],
                "observers": [{"id": k, "name": b.title, "goals": b.goals,
                               "members": [x["agent"] for x in roster if x["delegation"] == k]} for k, b in self.observer_briefs.items()],
                "rounds": rounds,
                "notice": "Researcher replay of saved agent actions. Offline-scripted models are not Gemini. Opening the viewer makes no LLM calls. Localhost only; no multi-user authentication."}

    @staticmethod
    def trace_overview(event: Event) -> dict:
        result = event.model_dump(mode="json")
        payload = result["payload"]
        payload["observed_count"] = len(payload.get("observations", []))
        payload.pop("observations", None)
        payload.pop("inputs", None)
        return result

    def round_one(self, scope: str = "public") -> dict:
        viewer = self.state["agents"].get(scope)
        if scope not in {"researcher", "public"} and viewer is None:
            raise PermissionError("Unknown viewer")
        # The continuous board does not need a full multi-round metrics/trace replay.
        visible = [e for e in self.events if e.round == 1 and e.kind in {
                   "internal_speech", "round_summary", "next_round_plan", "discussion_ended", "discussion_closed"}
                   and (scope == "researcher" or (e.visible_to(scope, viewer["delegation"])
                        if viewer else e.visibility == "public"))]
        trace_ids = {resolved: event.id for event in self.events
                     if event.kind == "agent_trace" and event.round == 1
                     and (scope == "researcher" or scope == event.actor)
                     for resolved in event.payload.get("resolution_event_ids", [])}
        countries = []
        for region, brief in self.briefs.items():
            members = []
            for key, agent in self.state["agents"].items():
                if agent["delegation"] != region:
                    continue
                item = {field: agent[field] for field in ("agent", "delegation", "role", "personality", "backend")}
                if scope == "researcher" or scope == key:
                    item.update(private_strategy=agent["private_strategy"], initial_preference=agent["initial_preference"])
                members.append(item)
            team_events = [e for e in visible if e.owners == [region]]
            discussion = [e.model_dump(mode="json") for e in team_events if e.kind == "internal_speech"]
            for message in discussion:
                if message["id"] in trace_ids:
                    message["trace_id"] = trace_ids[message["id"]]
            countries.append({"id": region, "name": brief.title,
                              "members": members, "discussion": discussion,
                              "summaries": [e.model_dump(mode="json") for e in team_events if e.kind == "round_summary"],
                              "action_plan": next((e.payload for e in reversed(team_events) if e.kind == "next_round_plan"), None),
                              "ending": next((e.payload for e in reversed(team_events) if e.kind == "discussion_ended"), None),
                              "completed": any(e.kind == "discussion_closed" for e in team_events)})
        return {"round": 1, "run": self.run_name, "backend": self.state["config"]["backend"],
                "mode": "recorded", "countries": countries}

    def round_one_agent(self, agent_id: str, scope: str = "public") -> dict:
        first = self.round_one(scope)
        agent = next((a for country in first["countries"] for a in country["members"]
                      if a["agent"] == agent_id), None)
        saved = self.state["agents"].get(agent_id)
        if agent is None and saved and saved["role"] == "observer":
            # Observers have no Round 1 team; they appear in bilaterals.
            agent = {field: saved[field] for field in ("agent", "delegation", "role", "personality", "backend")}
        if agent is None:
            raise PermissionError("Unknown agent")
        round_one_stages = {"prepare", "internal", "mandate", "vote", "announce", "summary", "plan"}
        traces = [self.trace(e.id, scope) for e in self.events
                  if e.kind == "agent_trace" and e.actor == agent_id
                  and ((e.round == 1 and e.payload.get("stage") in round_one_stages) or e.payload.get("stage") in BILATERAL_STAGES)
                  and (scope == "researcher" or scope == agent_id)]
        failures = [e.model_dump(mode="json") for e in self.events
                    if e.kind == "agent_call_failed" and e.actor == agent_id
                    and (e.round == 1 or e.payload.get("stage") in BILATERAL_STAGES)
                    and scope in {"researcher", agent_id}]
        groups: dict[tuple, list] = {}
        for event in traces:
            payload = event["payload"]
            key = (event["round"], "Bilaterals") if payload["stage"] in BILATERAL_STAGES else (1, None)
            groups.setdefault(key, []).append({"id": event["id"], "stage": payload["stage"], **self.thinking(payload)})
        bilateral_rounds = sum(title == "Bilaterals" for _, title in groups)
        thinking_rounds = [{"round": number, "traces": items, "title": None if title is None else
                            ("Bilaterals" if bilateral_rounds == 1 else f"Bilaterals before Round {number}")}
                           for (number, title), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))]
        return {"round": 1, "agent": agent, "traces": traces, "failures": failures,
                "thinking_rounds": thinking_rounds or [{"round": 1, "traces": []}]}

    @staticmethod
    def thinking(trace: dict) -> dict:
        """The thinking shown for one decision: provider thought summaries, else the agent's decision summary."""
        calls = trace.get("generation", {}).get("calls", [])
        accepted = next((call for call in reversed(calls) if call.get("accepted")), {})
        thoughts = accepted.get("thought_summaries", []) if accepted.get("attempt", 1) == 1 else []
        summary = trace.get("decision_summary") or {}
        return {"thoughts": thoughts or ([summary["summary"]] if summary.get("summary") else []),
                "source": "provider_summary" if thoughts else trace.get("summary_source", "not_recorded")}

    def participant_directory(self) -> list[dict]:
        names = {**{k: b.title for k, b in self.briefs.items()}, **{k: b.title for k, b in self.observer_briefs.items()}}
        return [{"agent": key, "delegation": a["delegation"], "delegation_name": names.get(a["delegation"], a["delegation"]),
                 "role": a["role"]} for key, a in self.state["agents"].items()]

    def bilaterals(self, scope: str = "public") -> dict:
        """Every bilateral the viewer may see, by round and slot, with links to the decision traces behind each message."""
        researcher = scope == "researcher"
        viewer = self.state["agents"].get(scope)
        if not researcher and scope != "public" and viewer is None:
            raise PermissionError("Unknown viewer")
        visible = [e for e in self.events if researcher or (viewer and e.visible_to(scope, viewer["delegation"]))]
        trace_events = {resolved: e for e in self.events if e.kind == "agent_trace" and (researcher or scope == e.actor)
                        for resolved in e.payload.get("resolution_event_ids", [])}
        traces = {resolved: e.id for resolved, e in trace_events.items()}
        thinking = lambda event_id: self.thinking(trace_events[event_id].payload) if event_id in trace_events else None
        rounds: dict[int, dict[int, dict]] = {}
        slot_of = lambda number, slot: rounds.setdefault(number, {}).setdefault(slot, {
            "slot": slot, "nominations": {}, "unmatched": [], "paired": False, "finished": False, "meetings": [], "summary": None})
        for event in visible:
            if event.kind == "bilateral_nominations":
                slot_of(event.round, event.payload["slot"])["nominations"][event.actor] = {
                    "requests": event.payload["requests"], "trace_id": traces.get(event.id)}
            elif event.kind == "bilateral_pairing":
                slot = slot_of(event.round, event.payload["slot"])
                slot.update(paired=True, unmatched=event.payload["unmatched"])
            elif event.kind == "bilateral_slot_finished":
                slot_of(event.round, event.payload["slot"])["finished"] = True
            elif event.kind == "bilateral_slot_summary":
                slot_of(event.round, event.payload["slot"])["summary"] = {"text": event.payload["summary"],
                    "trace_id": traces.get(event.id), "thinking": thinking(event.id)}
        relations = {e.round: e.payload for e in visible if e.kind == "bilateral_relations"}
        for meeting in fold_meetings(visible).values():
            for item in (*meeting["messages"], *meeting["reports"].values()):
                item.update(trace_id=traces.get(item["event_id"]), thinking=thinking(item["event_id"]))
            slot_of(meeting["round"], meeting["slot"])["meetings"].append(meeting)
        return {"mode": "recorded", "run": self.run_name, "backend": self.state["config"]["backend"],
                "participants": self.participant_directory(),
                "delegations": [{"id": k, "name": b.title} for k, b in self.briefs.items()],
                "rounds": [{"round": number, "slots": [slots[k] for k in sorted(slots)], "relations": relations.get(number)}
                           for number, slots in sorted(rounds.items())]}

    def trace(self, event_id: str, scope: str) -> dict:
        member = self.state["agents"].get(scope)
        if scope not in ("researcher", "public") and member is None:
            raise PermissionError("Unknown viewer")
        event = next((e for e in self.events if e.id == event_id and e.kind == "agent_trace"), None)
        if event is None or not (scope == "researcher" or
                                (member and event.visible_to(scope, member["delegation"]))):
            raise PermissionError("Decision trace is not available to this viewer")
        return event.model_dump(mode="json")

    def act(self, action: dict) -> dict:
        raise PermissionError("Recorded sessions are read-only")


def server_for(lab: Lab, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def valid_origin(self) -> bool:
            actual_port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
            return self.headers.get("Host") in hosts and self.headers.get("Origin") in (
                None, *(f"http://{host}" for host in hosts))

        def send(self, status: int, data: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, status: int, data: dict):
            self.send(status, json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            if not self.valid_origin():
                return self.send_json(403, {"error": "Local same-origin access only"})
            parsed = urlsplit(self.path)
            scope = parse_qs(parsed.query).get("scope", ["public"])[0]
            try:
                with lab.lock:
                    if parsed.path == "/api/replay":
                        return self.send_json(200, lab.replay(scope))
                    if parsed.path == "/api/round-one":
                        return self.send_json(200, lab.round_one(scope))
                    if parsed.path == "/api/round-one/agent":
                        agent_id = parse_qs(parsed.query).get("agent", [""])[0]
                        data = lab.round_one_agent(agent_id, scope)
                        if parse_qs(parsed.query).get("view") == ["thinking"]:
                            data = {"round": data["round"], "thinking_rounds": data["thinking_rounds"]}
                        return self.send_json(200, data)
                    if parsed.path == "/api/bilaterals":
                        return self.send_json(200, lab.bilaterals(scope))
                    if parsed.path == "/api/trace":
                        event_id = parse_qs(parsed.query).get("id", [""])[0]
                        return self.send_json(200, lab.trace(event_id, scope))
                files = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                         "/viz.js": ("viz.js", "text/javascript; charset=utf-8"),
                         "/styles.css": ("styles.css", "text/css; charset=utf-8")}
                if parsed.path not in files:
                    return self.send_json(404, {"error": "Not found"})
                filename, mime = files[parsed.path]
                self.send(200, (WEB / filename).read_bytes(), mime)
            except PermissionError as error:
                self.send_json(403, {"error": str(error)})
            except Exception:
                self.send_json(500, {"error": "Unable to load the research run"})

        def do_POST(self):
            if not self.valid_origin() or self.headers.get("X-Simpact-Lab") != "1":
                return self.send_json(403, {"error": "Local same-origin lab actions only"})
            if urlsplit(self.path).path != "/api/actions":
                return self.send_json(404, {"error": "Not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 16384:
                    raise ValueError("Invalid request size")
                action = json.loads(self.rfile.read(size))
                self.send_json(200, lab.act(action))
            except PermissionError as error:
                self.send_json(403, {"error": str(error)})
            except (ValueError, KeyError, TypeError) as error:
                self.send_json(400, {"error": str(error)})

        def log_message(self, _format: str, *_args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / "runs/climate-mvp-seed42")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--live", action="store_true", help="Create a live Round 1 session; calls begin when Start is pressed")
    parser.add_argument("--resume-live", type=Path, help="Restore a saved live Round 1 session, with all countries paused")
    parser.add_argument("--backend", choices=("gemini", "offline"), default="gemini")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--thinking-level", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--team-size", type=int, default=3)
    parser.add_argument("--world", choices=("six", "eight"), default="six", help="eight adds Brazil and Saudi Arabia to live Round 1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--message-budget", type=int, default=12, help="Messages per country before pausing; Continue renews the budget")
    parser.add_argument("--observers", nargs="*", choices=[o.value for o in ObserverId], default=[],
                        help="observer groups who join the bilaterals; no values means all three")
    parser.add_argument("--bilateral-slots", type=int, default=2, help="bilateral slots after Round 1")
    parser.add_argument("--bilateral-messages", type=int, default=8, help="message limit per bilateral conversation")
    args = parser.parse_args()
    if args.resume_live:
        args.live = True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    if args.live:
        from .live import LiveLab
        from .models import SimulationConfig
        import sys
        observers = args.observers if args.observers or "--observers" not in sys.argv else list(ObserverId)
        lab = LiveLab(SimulationConfig(seed=args.seed, team_size=args.team_size, backend=args.backend,
                                      default_model=args.model, world=args.world, observers=observers,
                                      bilateral_slots=args.bilateral_slots, bilateral_messages=args.bilateral_messages),
                      thinking_level=args.thinking_level,
                      message_budget=args.message_budget, resume=args.resume_live,
                      output=None if args.resume_live else ROOT / "runs" / f"round-one-live-{stamp}")
    else:
        lab = Lab(args.run)
    server = server_for(lab, args.port)
    print(f"Round 1 viewer: http://127.0.0.1:{server.server_address[1]}/", flush=True)
    print("Live session: Start a country to generate messages." if args.live else "Recorded delegation discussions and agent perspectives.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if args.live:
            lab.close()
        server.server_close()


if __name__ == "__main__":
    main()
