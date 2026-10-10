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
from .briefs import load_briefs, load_observers
from .meetings import MeetingHub, Participant
from .memory import Event, EventStore
from .metrics import dispersion
from .models import ClimateCommitment

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "frontend"


class Lab:
    def __init__(self, run: Path, *, output: Path | None = None):
        self.state = json.loads((run / "state.json").read_text())
        self.events = [Event.model_validate_json(line) for line in (run / "events.jsonl").read_text().splitlines()]
        config = self.state["config"]
        self.briefs = load_briefs(us_variant=config["us_variant"], world=config.get("world", "six"))
        self.observer_briefs = load_observers(config.get("observers", []))
        self.lock = threading.RLock()
        participants = [Participant(id=key, delegation=agent["delegation"], role=agent["role"],
                                   model=agent["backend"]) for key, agent in self.state["agents"].items()]
        if output:
            output.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.hub = MeetingHub(participants, store=EventStore(output / "meetings.jsonl" if output else None))
        self.human_role: str | None = None
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
            meetings = {e.payload["meeting"]["id"]: e.payload["meeting"] for e in round_events
                        if e.kind.startswith("meeting_") and "meeting" in e.payload}
            rounds[str(round_number)] = {"teams": teams, "meetings": list(meetings.values()),
                "traces": [self.trace_overview(e) for e in round_events if e.kind == "agent_trace"],
                "timeline": [self.trace_overview(e) if e.kind == "agent_trace" else e.model_dump(mode="json") for e in round_events if e.kind in (
                    "individual_preparation", "internal_speech", "agent_trace", "internal_ballot",
                    "national_mandate", "summit_speech", "meeting_requested", "meeting_acknowledged",
                    "meeting_declined", "meeting_message", "meeting_closed", "meeting_expired",
                    "meeting_outcome_reported", "team_meeting_report", "meeting_action_rejected", "observer_statement")],
                "notifications": [e.model_dump(mode="json") for e in round_events if e.kind == "team_meeting_report"],
                "public_events": [e.model_dump(mode="json") for e in round_events if e.visibility == "public" and e.kind in ("summit_speech", "observer_statement", "commitments", "negotiation_offer")],
                "climate_outcome": self.state["outcomes_by_round"].get(str(round_number))}
        return {"run": self.run_name, "scope": scope, "config": self.state["config"], "roster": roster,
                "delegations": [{"id": k, "name": b.title, "goals": b.goals,
                                "members": [x["agent"] for x in roster if x["delegation"] == k]} for k, b in self.briefs.items()],
                "observers": [{"id": k, "name": b.title, "goals": b.goals,
                               "members": [x["agent"] for x in roster if x["delegation"] == k]} for k, b in self.observer_briefs.items()],
                "rounds": rounds, "human_role": self.human_role,
                "notice": "Researcher replay of saved agent actions. Offline-scripted models are not Gemini. Meeting sandbox is manual; no LLM calls. Localhost only; no multi-user authentication."}

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
        if agent is None:
            raise PermissionError("Unknown agent")
        traces = [self.trace(e.id, scope) for e in self.events
                  if e.kind == "agent_trace" and e.round == 1 and e.actor == agent_id
                  and e.payload.get("stage") in {"prepare", "internal", "mandate", "vote", "announce", "summary", "plan"}
                  and (scope == "researcher" or scope == agent_id)]
        failures = [e.model_dump(mode="json") for e in self.events
                    if e.kind == "agent_call_failed" and e.round == 1 and e.actor == agent_id
                    and scope in {"researcher", agent_id}]
        thinking = []
        for event in traces:
            payload = event["payload"]
            calls = payload.get("generation", {}).get("calls", [])
            accepted = next((call for call in reversed(calls) if call.get("accepted")), {})
            thoughts = accepted.get("thought_summaries", []) if accepted.get("attempt", 1) == 1 else []
            summary = payload.get("decision_summary") or {}
            thinking.append({"id": event["id"], "stage": payload["stage"],
                "thoughts": thoughts or ([summary["summary"]] if summary.get("summary") else []),
                "source": "provider_summary" if thoughts else payload.get("summary_source", "not_recorded")})
        return {"round": 1, "agent": agent, "traces": traces, "failures": failures,
                "thinking_rounds": [{"round": 1, "traces": thinking}]}

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
        with self.lock:
            operation = action["operation"]
            actor = action.get("actor")
            if self.human_role and operation not in ("release", "advance") and actor != self.human_role:
                raise PermissionError("While playing a role, actions must use that participant")
            if operation == "request":
                self.hub.round_number = int(action.get("round", 1))
                if str(self.hub.round_number) not in self.state["commitments_by_round"]:
                    raise ValueError("Unknown round")
                self.hub.request(actor, action["invitees"], action["topic"])
            elif operation == "respond":
                if not isinstance(action.get("accepted"), bool):
                    raise ValueError("Acknowledgement must be a boolean")
                self.hub.respond(actor, action["meeting_id"], action["accepted"])
            elif operation == "message":
                self.hub.message(actor, action["meeting_id"], action["text"])
            elif operation == "close":
                self.hub.close(actor, action["meeting_id"])
            elif operation == "report":
                self.hub.report(actor, action["meeting_id"], action["summary"],
                    agreements=action.get("agreements", []), next_steps=action.get("next_steps", []),
                    notify_team=action.get("notify_team", True))
            elif operation == "advance":
                self.hub.advance(action.get("ticks", 1))
            elif operation == "claim":
                self.hub._known(actor)
                self.human_role = actor
                self.hub.participants[actor].controller = "human"
                self.hub.store.append("human_role_claimed", {"participant": actor, "controller": "human", "manual_sandbox": True},
                                      round=1, visibility="public", actor=actor)
            elif operation == "release":
                if self.human_role:
                    actor = self.human_role
                    self.hub.participants[actor].controller = "agent"
                    self.human_role = None
                    self.hub.store.append("human_role_released", {"participant": actor}, round=1, visibility="public", actor=actor)
            else:
                raise ValueError("Unknown meeting operation")
            return {"human_role": self.human_role, "tick": self.hub.tick}


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
                    if lab.human_role:
                        scope = lab.human_role
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
                    if parsed.path == "/api/trace":
                        event_id = parse_qs(parsed.query).get("id", [""])[0]
                        return self.send_json(200, lab.trace(event_id, scope))
                    if parsed.path == "/api/meetings":
                        if scope == "public":
                            data = {"tick": lab.hub.tick, "acknowledgement_ticks": lab.hub.acknowledgement_ticks,
                                    "participants": [p.model_dump(mode="json") for p in lab.hub.participants.values()], "meetings": [], "notifications": []}
                        else:
                            data = lab.hub.view(scope, researcher=scope == "researcher")
                            saved_notifications = [e.model_dump(mode="json") for e in lab.events
                                if e.kind == "team_meeting_report" and (scope == "researcher" or
                                    e.visible_to(scope, lab.state["agents"][scope]["delegation"]))]
                            data["notifications"] = saved_notifications + data["notifications"]
                        return self.send_json(200, {**data, "human_role": lab.human_role})
                files = {"/": ("index.html", "text/html; charset=utf-8"),
                         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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
    args = parser.parse_args()
    if args.resume_live:
        args.live = True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    if args.live:
        from .live import LiveLab
        from .models import SimulationConfig
        lab = LiveLab(SimulationConfig(seed=args.seed, team_size=args.team_size, backend=args.backend,
                                      default_model=args.model, world=args.world), thinking_level=args.thinking_level,
                      message_budget=args.message_budget, resume=args.resume_live,
                      output=None if args.resume_live else ROOT / "runs" / f"round-one-live-{stamp}")
    else:
        lab = Lab(args.run, output=ROOT / "runs" / f"meeting-lab-{stamp}")
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
