"""Model UN simulation: country delegations debate an agenda item, negotiate, draft resolutions and vote.

    uv run model_un.py                                        # default scenario, all phases
    uv run model_un.py --scenario scenarios/my_topic.toml --phases 2
"""

import argparse
import asyncio
import json
import tomllib
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from autogen_core import MessageContext, RoutedAgent, SingleThreadedAgentRuntime, TopicId, TypeSubscription, message_handler
from autogen_core.tools import FunctionTool, Tool

from gemini import gemini_client, web_search
from swarm import (
    BROADCAST_TOPIC,
    TOPIC_SOURCE,
    AgentCard,
    Broadcast,
    Directory,
    PersonaAgent,
    TranscriptRecorder,
    agent_id,
    log,
    register_persona,
)

FLOOR_TOPIC = "floor"  # public speeches: every delegation and the Secretariat subscribe
SECRETARIAT = "secretariat"


# --- Messages specific to the committee ----------------------------------------------------------------


@dataclass
class FloorSpeech:
    speaker: str
    text: str


@dataclass
class SubmitDraft:
    sponsor: str
    title: str
    operative_clauses: str
    co_sponsors: list[str]


@dataclass
class CastVote:
    voter: str
    draft_id: str
    vote: str
    explanation: str


# --- Secretariat: the official record, plain code, no LLM ---------------------------------------------------


class Secretariat(RoutedAgent):
    def __init__(self, delegations: set[str]) -> None:
        super().__init__("UN Secretariat: records speeches, registers drafts, counts votes")
        self.delegations = delegations
        self.speeches: list[FloorSpeech] = []
        self.drafts: dict[str, SubmitDraft] = {}
        self.votes: dict[str, dict[str, CastVote]] = {}

    @message_handler
    async def on_speech(self, message: FloorSpeech, ctx: MessageContext) -> None:
        self.speeches.append(message)

    # Drafts and votes arrive as RPCs (agent.send_message), so the delegate gets the result back immediately.

    @message_handler
    async def on_draft(self, message: SubmitDraft, ctx: MessageContext) -> str:
        if unknown := [c for c in message.co_sponsors if c not in self.delegations]:
            return f"error: unknown co-sponsors {unknown}"
        message.co_sponsors = [c for c in message.co_sponsors if c != message.sponsor]
        draft_id = next((i for i, d in self.drafts.items() if d.sponsor == message.sponsor), f"L.{len(self.drafts) + 1}")
        self.drafts[draft_id] = message  # a sponsor's resubmission replaces their earlier draft
        return f"registered as draft {draft_id}"

    @message_handler
    async def on_vote(self, message: CastVote, ctx: MessageContext) -> str:
        if message.draft_id not in self.drafts:
            return f"error: no draft {message.draft_id!r}; drafts are {list(self.drafts)}"
        self.votes.setdefault(message.draft_id, {})[message.voter] = message
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
            count = {v: sum(1 for x in votes.values() if x.vote == v) for v in ("yes", "no", "abstain")}
            out[draft_id] = {**count, "adopted": count["yes"] > count["no"],
                             "votes": {name: asdict(v) for name, v in votes.items()}}
        return out

    async def save_state(self) -> dict:
        return {"speeches": [asdict(s) for s in self.speeches],
                "drafts": {i: asdict(d) for i, d in self.drafts.items()}, "results": self.results()}


# --- Delegate: a PersonaAgent with committee tools -----------------------------------------------------


class Delegate(PersonaAgent):
    def __init__(self, *, committee: str, agenda: str, **kwargs) -> None:
        self._committee = committee
        self._agenda = agenda
        self._floor: deque[FloorSpeech] = deque(maxlen=15)
        super().__init__(**kwargs)

    def extra_tools(self) -> list[Tool]:
        return [
            FunctionTool(self.speak_to_floor, name="speak_to_floor", description="Give a speech that every delegation hears."),
            FunctionTool(self.submit_draft, name="submit_draft",
                         description="Submit your draft resolution to the Secretariat (resubmitting replaces it)."),
            FunctionTool(self.cast_vote, name="cast_vote", description="Vote on a draft resolution."),
        ]

    def extra_context(self) -> str:
        floor = "\n".join(f"- {s.speaker}: {s.text}" for s in self._floor) or "(no speeches yet)"
        return (f"\n\nCommittee: {self._committee}\nAgenda: {self._agenda}\n"
                f"Speeches reach every delegation; private messages reach only the recipient.\n\n"
                f"Recent speeches on the floor:\n{floor}")

    @message_handler
    async def on_floor(self, message: FloorSpeech, ctx: MessageContext) -> None:
        # Listening is free: speeches land in the prompt context but do not trigger an LLM call.
        if message.speaker != self.card.name:
            self._floor.append(message)

    async def speak_to_floor(self, speech: str) -> str:
        log(f"  [FLOOR] {self.card.name}: {speech}")
        await self.publish_message(FloorSpeech(self.card.name, speech), TopicId(FLOOR_TOPIC, TOPIC_SOURCE))
        return "delivered to all delegations"

    async def submit_draft(self, title: str, operative_clauses: str, co_sponsors: list[str]) -> str:
        reply = await self.send_message(
            SubmitDraft(self.card.name, title, operative_clauses, co_sponsors), agent_id(SECRETARIAT)
        )
        log(f"  [DRAFT] {self.card.name}: \"{title}\" (co-sponsors: {', '.join(co_sponsors) or 'none'}) -> {reply}")
        return reply

    async def cast_vote(self, draft_id: str, vote: Literal["yes", "no", "abstain"], explanation: str) -> str:
        reply = await self.send_message(CastVote(self.card.name, draft_id, vote, explanation), agent_id(SECRETARIAT))
        log(f"  [VOTE] {self.card.name} votes {vote} on {draft_id}: {explanation} -> {reply}")
        return reply


def persona(d: dict) -> str:
    return (f"Your objectives:\n{d['objectives'].strip()}\n"
            f"Your red lines (never accept): {d['red_lines']}\n"
            f"Negotiating style: {d['style']}\n"
            "Stay in character as a diplomat. Your objectives and red lines are confidential unless revealing "
            "them helps you.")


# --- The session ----------------------------------------------------------------------------------------------


async def main(scenario_path: Path, max_phases: int | None) -> None:
    sc = tomllib.loads(scenario_path.read_text())
    run_dir = Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)

    runtime = SingleThreadedAgentRuntime(intervention_handlers=[TranscriptRecorder(run_dir / "events.jsonl")])
    directory = Directory()
    model = gemini_client()
    search = FunctionTool(web_search, description="Search the web for current facts and figures.")

    names = {d["name"] for d in sc["delegations"]}
    await Secretariat.register(runtime, SECRETARIAT, lambda: Secretariat(names))
    await runtime.add_subscription(TypeSubscription(topic_type=FLOOR_TOPIC, agent_type=SECRETARIAT))
    for d in sc["delegations"]:
        await register_persona(
            runtime, Delegate, AgentCard(d["name"], d["title"], d["public_position"]), directory,
            topics=(FLOOR_TOPIC,), persona=persona(d), model_client=model, tools=[search],
            max_hops=sc.get("max_hops", 4), committee=sc["committee"], agenda=sc["agenda"],
        )
    secretariat = await runtime.try_get_underlying_agent_instance(agent_id(SECRETARIAT), Secretariat)

    log(f"{sc['committee']}\nAgenda: {sc['agenda']}\n\nDelegations:\n{directory.render()}")
    phases = sc["phases"][:max_phases]
    for n, phase in enumerate(phases, 1):
        # One phase = one Chair broadcast; agents act and talk until the bus is quiet, then the next phase starts.
        text = phase["instruction"].format(agenda=sc["agenda"], committee=sc["committee"],
                                           drafts=secretariat.render_drafts())
        log(f"\n== PHASE {n}/{len(phases)}: {phase['name']}")
        runtime.start()
        await runtime.publish_message(
            Broadcast(f"PHASE {n}/{len(phases)}: {phase['name']}\n{text}", sender="Chair"),
            TopicId(BROADCAST_TOPIC, TOPIC_SOURCE),
        )
        await runtime.stop_when_idle()

    results = secretariat.results()
    log("\n== RESULTS")
    for draft_id, r in results.items():
        verdict = "ADOPTED" if r["adopted"] else "REJECTED"
        log(f"Draft {draft_id} \"{secretariat.drafts[draft_id].title}\": "
            f"{r['yes']} yes / {r['no']} no / {r['abstain']} abstain -> {verdict}")
    if not results:
        log("No drafts were submitted.")

    (run_dir / "state.json").write_text(json.dumps(await runtime.save_state(), indent=2, default=str))
    log(f"\nRecord: {run_dir}/state.json (secretariat/sim has speeches, drafts, votes); "
        f"every message: {run_dir}/events.jsonl")
    await runtime.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", type=Path, default=Path("scenarios/loss_and_damage.toml"))
    p.add_argument("--phases", type=int, default=None, help="run only the first N phases")
    a = p.parse_args()
    asyncio.run(main(a.scenario, a.phases))
