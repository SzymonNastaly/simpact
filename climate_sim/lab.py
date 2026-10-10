"""Local agent-dynamics lab: private replay inspection and a meeting protocol sandbox.

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
from .briefs import load_briefs
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
        self.briefs = load_briefs(us_variant=self.state["config"]["us_variant"])
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
        for round_number in sorted({e.round for e in self.events if e.kind == "commitments"}):
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
            # Agent meetings of this round, in their latest recorded state. Meeting events are scoped to their exact
            # participants, so `visible` already holds only what this perspective may see.
            meetings = {e.payload["meeting"]["id"]: e.payload["meeting"] for e in round_events if e.kind.startswith("meeting_")}
            rounds[str(round_number)] = {"teams": teams, "meetings": list(meetings.values()),
                "public_events": [e.model_dump(mode="json") for e in round_events if e.visibility == "public" and e.kind in ("summit_speech", "commitments", "negotiation_offer")],
                "climate_outcome": self.state["outcomes_by_round"].get(str(round_number))}
        return {"run": self.run_name, "scope": scope, "config": self.state["config"], "roster": roster,
                "delegations": [{"id": k, "name": b.title, "goals": b.goals,
                                "members": [x["agent"] for x in roster if x["delegation"] == k]} for k, b in self.briefs.items()],
                "rounds": rounds, "human_role": self.human_role,
                "notice": "Researcher replay of saved agent actions. Offline-scripted models are not Gemini. Meeting sandbox is manual; no LLM calls. Localhost only; no multi-user authentication."}

    def act(self, action: dict) -> dict:
        with self.lock:
            operation = action["operation"]
            actor = action.get("actor")
            if self.human_role and operation not in ("release", "advance") and actor != self.human_role:
                raise PermissionError("While playing a role, actions must use that participant")
            if operation == "request":
                self.hub.request(actor, action["invitees"], action["topic"])
            elif operation == "respond":
                if not isinstance(action.get("accepted"), bool):
                    raise ValueError("Acknowledgement must be a boolean")
                self.hub.respond(actor, action["meeting_id"], action["accepted"])
            elif operation == "message":
                self.hub.message(actor, action["meeting_id"], action["text"])
            elif operation == "close":
                self.hub.close(actor, action["meeting_id"])
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
                    if parsed.path == "/api/meetings":
                        if scope == "public":
                            data = {"tick": lab.hub.tick, "acknowledgement_ticks": lab.hub.acknowledgement_ticks,
                                    "participants": [p.model_dump(mode="json") for p in lab.hub.participants.values()], "meetings": []}
                        else:
                            data = lab.hub.view(scope, researcher=scope == "researcher")
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
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
    lab = Lab(args.run, output=ROOT / "runs" / f"meeting-lab-{stamp}")
    server = server_for(lab, args.port)
    print(f"Agent dynamics lab: http://127.0.0.1:{server.server_address[1]}/", flush=True)
    print("Local researcher workspace. Manual meeting sandbox; no Gemini calls. Not for shared/public deployment.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
