"""Model UN simulation: country delegations debate an agenda item, negotiate, draft resolutions and vote.

    uv run model_un.py                                        # default scenario, all phases
    uv run model_un.py --scenario scenarios/my_topic.toml --phases 2
    uv run model_un.py --resume runs/<timestamp>/state.json   # continue a run after its last completed round
"""

import argparse
import dataclasses
import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components.game_master import event_resolution, make_observation, switch_act
from concordia.environment import engine as engine_lib
from concordia.environment.engines import simultaneous
from concordia.language_model import language_model
from concordia.prefabs.simulation import generic as simulation
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from concordia.typing import prefab as prefab_lib
from concordia.utils import structured_logging

from gemini import gemini_model, web_search
from swarm import MEMORY_KEY, FunctionTool, PersonaAgent, log, no_embedding

SECRETARIAT = "secretariat"
VOTES = ("yes", "no", "abstain")

# The engine asks the game master four questions besides "what does X observe": is the session over, who acts next,
# in what format, and what did their actions cause. The Chair answers all four, so it sits under all four keys.
CHAIR_KEYS = (
    switch_act.DEFAULT_TERMINATE_COMPONENT_KEY,
    switch_act.DEFAULT_NEXT_ACTING_COMPONENT_KEY,
    switch_act.DEFAULT_NEXT_ACTION_SPEC_COMPONENT_KEY,
    switch_act.DEFAULT_RESOLUTION_COMPONENT_KEY,
)

MOVES = """Answer with one JSON object and nothing else:
{"moves": [<your moves, in order>], "thought": "<your private reasoning; nobody else reads it>"}
The moves you can make, any number of them (an empty list means you stay silent this round):
{"move": "speak_to_floor", "speech": "..."}  give a speech that every delegation hears
{"move": "send_message", "to": "<delegation>", "message": "..."}  send a private message only the recipient reads
{"move": "submit_draft", "title": "...", "operative_clauses": "...", "co_sponsors": ["<delegation>", ...]}  submit your draft resolution to the Secretariat (resubmitting replaces it)
{"move": "cast_vote", "draft_id": "L.1", "vote": "yes" | "no" | "abstain", "explanation": "..."}  vote on a draft resolution"""


# --- The official record ---------------------------------------------------------------------------------------


@dataclass
class Speech:
    speaker: str
    text: str


@dataclass
class Draft:
    sponsor: str
    title: str
    operative_clauses: str
    co_sponsors: list[str]


@dataclass
class Vote:
    voter: str
    draft_id: str
    vote: str
    explanation: str


class Secretariat(entity_component.ContextComponent):
    """The official record of speeches, drafts and votes. Plain code, no LLM, so the results don't depend on what
    delegations remember."""

    def __init__(self, delegations: Sequence[str]) -> None:
        super().__init__()
        self._delegations = list(delegations)
        self.speeches: list[Speech] = []
        self.drafts: dict[str, Draft] = {}
        self.votes: dict[str, dict[str, Vote]] = {}

    def record_speech(self, speaker: str, text: str) -> None:
        self.speeches.append(Speech(speaker, text))

    def submit_draft(self, sponsor: str, title: str, operative_clauses: str, co_sponsors: list[str]) -> str:
        if unknown := [c for c in co_sponsors if c not in self._delegations]:
            return f"error: unknown co-sponsors {unknown}"
        co_sponsors = [c for c in co_sponsors if c != sponsor]
        draft_id = next((i for i, d in self.drafts.items() if d.sponsor == sponsor), f"L.{len(self.drafts) + 1}")
        self.drafts[draft_id] = Draft(sponsor, title, operative_clauses, co_sponsors)  # a resubmission replaces it
        return f"registered as draft {draft_id}"

    def cast_vote(self, voter: str, draft_id: str, vote: str, explanation: str) -> str:
        if draft_id not in self.drafts:
            return f"error: no draft {draft_id!r}; drafts are {list(self.drafts)}"
        if vote not in VOTES:
            return f"error: vote must be one of {list(VOTES)}"
        self.votes.setdefault(draft_id, {})[voter] = Vote(voter, draft_id, vote, explanation)
        return "vote recorded"

    def render_drafts(self) -> str:
        if not self.drafts:
            return "(no drafts were submitted)"
        return "\n\n".join(
            f"Draft {i} \"{d.title}\" sponsored by {d.sponsor}; co-sponsors: {', '.join(d.co_sponsors) or 'none'}\n"
            f"{d.operative_clauses}"
            for i, d in self.drafts.items()
        )

    def results(self) -> dict:
        out = {}
        for draft_id in self.drafts:
            votes = self.votes.get(draft_id, {})
            count = {v: sum(1 for x in votes.values() if x.vote == v) for v in VOTES}
            out[draft_id] = {**count, "adopted": count["yes"] > count["no"],
                             "votes": {name: asdict(v) for name, v in votes.items()}}
        return out

    def get_state(self) -> entity_component.ComponentState:
        return {"speeches": [asdict(s) for s in self.speeches],
                "drafts": {i: asdict(d) for i, d in self.drafts.items()},
                "votes": {i: {n: asdict(v) for n, v in votes.items()} for i, votes in self.votes.items()},
                "results": self.results()}

    def set_state(self, state: entity_component.ComponentState) -> None:
        self.speeches = [Speech(**s) for s in state["speeches"]]
        self.drafts = {i: Draft(**d) for i, d in state["drafts"].items()}
        self.votes = {i: {n: Vote(**v) for n, v in votes.items()} for i, votes in state["votes"].items()}


# --- The Chair: runs the session, plain code, no LLM --------------------------------------------------------------


def parse_turn(answer: str) -> dict:
    """A delegation's turn: one JSON object with a list of moves and an optional private thought."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0:
        raise ValueError("no JSON object found")
    try:
        turn = json.loads(answer[start:end + 1], strict=False)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON ({e})") from None
    if not isinstance(turn, dict) or not isinstance(turn.get("moves"), list):
        raise ValueError('expected {"moves": [...], "thought": "..."}')
    return turn


class Chair(entity_component.ContextComponent):
    """Moves the session through its phases and carries out the delegations' moves.

    Each phase opens with every delegation acting at once. After that, only delegations that received a private
    message act again, so a phase ends when everyone has gone quiet, or after `max_hops` rounds of replies.
    Speeches and private messages reach the other delegations as observations at their next turn; each delegation
    is also told what its own moves did (the Secretariat's replies included).
    """

    def __init__(self, delegations: Sequence[str], committee: str, agenda: str, phases: Sequence[Mapping[str, str]],
                 max_hops: int, events_path: str = "") -> None:
        super().__init__()
        self._delegations = list(delegations)
        self._committee = committee
        self._agenda = agenda
        self._phases = list(phases)
        self._max_hops = max_hops
        self._events_path = events_path
        self._phase = 0  # index of the current phase
        self._round = 0  # rounds completed in the current phase; round 0 is everyone's, later ones are replies
        self._due: list[str] = []  # who acts in the next round; empty until the phase has started
        self._announcement = ""  # the current phase's instructions, as the Chair announced them
        self._putative = ""  # the round's actions, as the engine hands them over before asking to resolve them
        self._round_log: list[str] = []

    def _secretariat(self) -> Secretariat:
        return self.get_entity().get_component(SECRETARIAT, type_=Secretariat)

    def _tell(self, delegation: str, text: str) -> None:
        self.get_entity().get_component(
            make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY, type_=make_observation.MakeObservation
        ).add_to_queue(delegation, text)

    def _record(self, kind: str, sender: str, line: str, **data: Any) -> None:
        """Prints a line to the console, adds it to the round's resolution, and appends the event to events.jsonl."""
        log(line)
        self._round_log.append(line.strip())
        if self._events_path:
            event = {"t": datetime.now().isoformat(timespec="seconds"), "phase": self._phases[self._phase]["name"],
                     "round": self._round + 1, "kind": kind, "sender": sender, **data}
            with open(self._events_path, "a") as f:
                f.write(json.dumps(event) + "\n")

    # Phases and rounds.

    def _start_phase(self, n: int) -> None:
        self._phase, self._round = n, 0
        if n >= len(self._phases):
            self._due = []
            return
        phase = self._phases[n]
        heading = f"PHASE {n + 1}/{len(self._phases)}: {phase['name']}"
        text = phase["instruction"].format(agenda=self._agenda, committee=self._committee,
                                           drafts=self._secretariat().render_drafts())
        self._announcement = f"{heading}\n{text}"
        self._record("phase", "Chair", f"\n== {heading}", text=text)
        for name in self._delegations:
            self._tell(name, f"[BROADCAST from the Chair] {heading} begins.")
        self._due = list(self._delegations)

    def _end_round(self, messaged: set[str]) -> None:
        self._round += 1
        due = [d for d in self._delegations if d in messaged]
        if due:
            self._due = due
        else:
            self._start_phase(self._phase + 1)

    def _call_to_action(self) -> str:
        replies = ""
        if self._round:
            replies = (f"\n\nReply round {self._round} of at most {self._max_hops}: "
                       "you have new private messages (see above).")
        if self._round >= self._max_hops:
            replies += " Private messages can no longer be sent in this phase."
        return f"{self._announcement}{replies}\n\nWhat does {{name}} do now? {MOVES}"

    # Carrying out moves.

    def _resolve(self) -> str:
        turns = {}
        for line in self._putative.splitlines():
            name, _, answer = line.partition(": ")
            if name in self._delegations:
                turns[name] = answer
        self._putative = ""
        self._round_log = []
        tag = f"[{self._phases[self._phase]['name']}, round {self._round + 1}]"
        messaged: set[str] = set()
        for name in self._delegations:  # fixed order, so draft numbers don't depend on which LLM call returned first
            if turns.get(name):  # an empty answer means the delegation's LLM call failed; it already logged why
                self._play_turn(name, turns[name], tag, messaged)
        summary = "\n".join(self._round_log) or "Nobody made a move."
        self._end_round(messaged)
        return f"{tag}\n{summary}"

    def _play_turn(self, name: str, answer: str, tag: str, messaged: set[str]) -> None:
        try:
            turn = parse_turn(answer)
        except ValueError as e:
            self._record("unreadable", name, f"  {name}: unreadable turn, {e}: {answer[:200]}", answer=answer)
            self._tell(name, f"{tag} The Chair could not read your last turn ({e}), so nothing happened.")
            return
        feedback = []
        for move in turn["moves"]:
            try:
                feedback.append(self._play_move(name, move, messaged, tag))
            except (KeyError, TypeError, AttributeError):
                feedback.append(f"error: malformed move {json.dumps(move)}")
        if thought := turn.get("thought"):
            log(f"  {name} (thought): {thought}")
            feedback.append(f"you thought privately: {thought}")
        if feedback:
            self._tell(name, f"{tag} Your turn: " + " | ".join(feedback))

    def _play_move(self, name: str, move: Mapping[str, Any], messaged: set[str], tag: str) -> str:
        """Carries out one move and returns what the delegation is told about it."""
        secretariat = self._secretariat()
        match move.get("move"):
            case "speak_to_floor":
                speech = str(move["speech"])
                secretariat.record_speech(name, speech)
                for other in self._delegations:
                    if other != name:
                        self._tell(other, f"{tag} {name} on the floor: {speech}")
                self._record("speech", name, f"  [FLOOR] {name}: {speech}", speech=speech)
                return f'you said on the floor: "{speech}"'
            case "send_message":
                to, message = str(move["to"]), str(move["message"])
                if to not in self._delegations:
                    return f"error: no delegation named {to!r}; delegations are {self._delegations}"
                if to == name:
                    return "error: you cannot message yourself"
                if self._round >= self._max_hops:
                    return f"error: conversation chain too long, wait for the next phase (message to {to} not sent)"
                self._tell(to, f"{tag} Private message from {name}: {message}")
                messaged.add(to)
                self._record("message", name, f"  {name} -> {to}: {message}", to=to, message=message)
                return f'you told {to} privately: "{message}"'
            case "submit_draft":
                title, clauses = str(move["title"]), str(move["operative_clauses"])
                co_sponsors = move.get("co_sponsors") or []
                co_sponsors = [co_sponsors] if isinstance(co_sponsors, str) else [str(c) for c in co_sponsors]
                reply = secretariat.submit_draft(name, title, clauses, co_sponsors)
                self._record("draft", name,
                             f"  [DRAFT] {name}: \"{title}\" (co-sponsors: {', '.join(co_sponsors) or 'none'}) -> {reply}",
                             title=title, operative_clauses=clauses, co_sponsors=co_sponsors, reply=reply)
                return f'you submitted the draft "{title}"; the Secretariat replied: {reply}'
            case "cast_vote":
                draft_id, vote = str(move["draft_id"]), str(move["vote"]).strip().lower()
                explanation = str(move.get("explanation", ""))
                reply = secretariat.cast_vote(name, draft_id, vote, explanation)
                self._record("vote", name, f"  [VOTE] {name} votes {vote} on {draft_id}: {explanation} -> {reply}",
                             draft_id=draft_id, vote=vote, explanation=explanation, reply=reply)
                return f"you voted {vote} on {draft_id}; the Secretariat replied: {reply}"
            case other:
                return f"error: unknown move {other!r}"

    # Concordia hooks. pre_act runs for every question the engine asks the game master; answer only our four.

    def pre_observe(self, observation: str) -> str:
        if observation.startswith(event_resolution.PUTATIVE_EVENT_TAG):
            self._putative = observation.removeprefix(event_resolution.PUTATIVE_EVENT_TAG).strip()
        return ""

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        match action_spec.output_type:
            case entity_lib.OutputType.TERMINATE:
                if not self._due:  # first step, or resumed from a run that stopped after its last phase
                    self._start_phase(self._phase)
                return "Yes" if self._phase >= len(self._phases) else "No"
            case entity_lib.OutputType.NEXT_ACTING:
                return ",".join(self._due)
            case entity_lib.OutputType.NEXT_ACTION_SPEC:
                spec = entity_lib.free_action_spec(call_to_action=self._call_to_action(), tag="action")
                return engine_lib.action_spec_to_string(spec)
            case entity_lib.OutputType.RESOLVE:
                return self._resolve()
        return ""

    def get_state(self) -> entity_component.ComponentState:
        return {"phase": self._phase, "round": self._round, "due": list(self._due),
                "announcement": self._announcement}

    def set_state(self, state: entity_component.ComponentState) -> None:
        self._phase, self._round = state["phase"], state["round"]
        self._due, self._announcement = list(state["due"]), state["announcement"]


@dataclasses.dataclass
class ChairGameMaster(prefab_lib.Prefab):
    """The game master of a Model UN session: the Chair, the Secretariat, and an observation queue."""

    description: str = "Runs a Model UN session: phases, turns, speeches, private messages, drafts and votes."
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "Chair",
            "committee": "",
            "agenda": "",
            "phases": [],  # [{"name", "instruction"}], run in order
            "max_hops": 4,
            "events_path": "",  # where to append every move as a JSON line
        }
    )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        p = self.params
        names = [e.name for e in self.entities]
        chair = Chair(names, p["committee"], p["agenda"], p["phases"], p.get("max_hops", 4), p.get("events_path", ""))
        components = {
            SECRETARIAT: Secretariat(names),
            # Delivers queued observations only: with the LLM fallback off, nobody observes anything we didn't send.
            make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY: make_observation.MakeObservation(
                model=model, player_names=names, allow_llm_fallback=False
            ),
            **dict.fromkeys(CHAIR_KEYS, chair),
        }
        # Every engine question has a component that answers it, so SwitchAct never calls the model.
        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=p.get("name", "Chair"),
            act_component=switch_act.SwitchAct(model=model, entity_names=names),
            context_components=components,
        )


def persona(d: dict) -> str:
    return (f"Your objectives:\n{d['objectives'].strip()}\n"
            f"Your red lines (never accept): {d['red_lines']}\n"
            f"Negotiating style: {d['style']}\n"
            "Stay in character as a diplomat. Your objectives and red lines are confidential unless revealing "
            "them helps you.")


# --- The session ----------------------------------------------------------------------------------------------


def main(scenario_path: Path, max_phases: int | None, resume: Path | None, verbose: bool) -> None:
    sc = tomllib.loads(scenario_path.read_text())
    run_dir = Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)

    search = FunctionTool(web_search, "web_search", "Search the web for current facts and figures. Args: query (str)")
    directory = [{"name": d["name"], "role": d["title"], "description": d["public_position"]}
                 for d in sc["delegations"]]
    context = (f"Committee: {sc['committee']}\nAgenda: {sc['agenda']}\n"
               "Speeches reach every delegation; private messages reach only the recipient.")
    instances = [
        prefab_lib.InstanceConfig(
            prefab="delegate__Entity", role=prefab_lib.Role.ENTITY,
            params={"name": d["name"], "role": d["title"], "persona": persona(d), "context": context,
                    "directory": directory, "tools": [search]},
        )
        for d in sc["delegations"]
    ]
    phases = sc["phases"][:max_phases]
    max_hops = sc.get("max_hops", 4)
    instances.append(prefab_lib.InstanceConfig(
        prefab="chair__GameMaster", role=prefab_lib.Role.GAME_MASTER,
        params={"name": "Chair", "committee": sc["committee"], "agenda": sc["agenda"], "phases": phases,
                "max_hops": max_hops, "events_path": str(run_dir / "events.jsonl")},
    ))
    config = prefab_lib.Config(
        prefabs={"delegate__Entity": PersonaAgent(), "chair__GameMaster": ChairGameMaster()}, instances=instances
    )
    # Simultaneous engine: everyone due in a round acts at once (in parallel threads), then the Chair resolves.
    engine = simultaneous.Simultaneous()
    sim = simulation.Simulation(config=config, model=gemini_model(), embedder=no_embedding, engine=engine)
    if resume:
        sim.load_from_checkpoint(json.loads(resume.read_text()))
        log(f"Resuming from {resume}")
    raw_log = sim.get_raw_log()  # step-by-step log for the HTML viewer, continued when resuming

    def save_state(step: int) -> None:
        # Concordia's checkpoint: every component's state (memories, notes, the Secretariat's record, the Chair's
        # position in the agenda). Overwritten after each round, so a crashed run can be resumed from it.
        (run_dir / "state.json").write_text(json.dumps({**sim.make_checkpoint_data(), "raw_log": raw_log},
                                                          indent=2, default=str))

    log(f"{sc['committee']}\nAgenda: {sc['agenda']}\n\nDelegations:\n"
        + "\n".join(f"- {c['name']} ({c['role']}): {c['description']}" for c in directory))
    # sim.play() would also work, but it always prints Concordia's step-by-step trace; --verbose turns that on.
    engine.run_loop(
        game_masters=sim.get_game_masters(), entities=sim.get_entities(), premise="",
        max_steps=len(phases) * (max_hops + 1), verbose=verbose, log=raw_log, checkpoint_callback=save_state,
    )

    chair = sim.get_game_masters()[0]
    secretariat = chair.get_component(SECRETARIAT, type_=Secretariat)
    results = secretariat.results()
    log("\n== RESULTS")
    for draft_id, r in results.items():
        verdict = "ADOPTED" if r["adopted"] else "REJECTED"
        log(f"Draft {draft_id} \"{secretariat.drafts[draft_id].title}\": "
            f"{r['yes']} yes / {r['no']} no / {r['abstain']} abstain -> {verdict}")
    if not results:
        log("No drafts were submitted.")

    save_state(-1)
    sim_log = structured_logging.SimulationLog.from_raw_log(raw_log)
    sim_log.attach_memories(
        entity_memories={e.name: list(e.get_component(MEMORY_KEY).get_all_memories_as_text())
                         for e in sim.get_entities()},
    )
    (run_dir / "log.html").write_text(sim_log.to_html(title=sc["committee"]))
    log(f"\nRecord: {run_dir}/state.json (the Chair's secretariat component has speeches, drafts, votes); "
        f"every move: {run_dir}/events.jsonl; every prompt, step by step: {run_dir}/log.html")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", type=Path, default=Path("scenarios/loss_and_damage.toml"))
    p.add_argument("--phases", type=int, default=None, help="run only the first N phases")
    p.add_argument("--resume", type=Path, default=None, help="state.json of an earlier run to continue from")
    p.add_argument("--verbose", action="store_true", help="also print Concordia's step-by-step engine trace")
    a = p.parse_args()
    main(a.scenario, a.phases, a.resume, a.verbose)
