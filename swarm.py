"""Generic persona agents on AutoGen Core: 1:1 messages, orchestrator broadcasts, an agent index, memory and tools.

AutoGen Core supplies the runtime, the message routing (topics + subscriptions) and the LLM clients.
What is ours: the message dataclasses, the agent index, and the per-agent think/tool loop.
Scenarios (e.g. model_un.py) subclass PersonaAgent to add their own tools, handlers and prompt context.
"""

import asyncio
import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from autogen_core import (
    AgentId,
    DefaultInterventionHandler,
    FunctionCall,
    MessageContext,
    RoutedAgent,
    TopicId,
    TypeSubscription,
    message_handler,
)
from autogen_core.memory import ListMemory, MemoryContent, MemoryMimeType
from autogen_core.model_context import BufferedChatCompletionContext
from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    FunctionExecutionResult,
    FunctionExecutionResultMessage,
    SystemMessage,
    UserMessage,
)
from autogen_core.tools import FunctionTool, Tool

BROADCAST_TOPIC = "broadcast"
TOPIC_SOURCE = "sim"  # TypeSubscription maps topic source -> agent key, so every agent lives at AgentId(<name>, "sim")


def inbox_topic(agent_name: str) -> TopicId:
    return TopicId(type=f"inbox.{agent_name}", source=TOPIC_SOURCE)


def agent_id(name: str) -> AgentId:
    return AgentId(name, TOPIC_SOURCE)


# --- Messages on the bus -------------------------------------------------------------------------


@dataclass
class DirectMessage:
    sender: str
    recipient: str
    content: str
    hops: int = 0  # length of the agent-to-agent reply chain, used to stop endless ping-pong


@dataclass
class Broadcast:
    content: str
    sender: str = "orchestrator"


# --- Agent index -------------------------------------------------------------------------------------


@dataclass
class AgentCard:
    name: str  # agent type id: letters, digits, _ - . only
    role: str
    description: str  # public: every agent sees this in the index


@dataclass
class Directory:
    """Index of every agent in the simulation. AutoGen Core has no public 'list agents' API, so we keep one."""

    cards: dict[str, AgentCard] = field(default_factory=dict)

    def add(self, card: AgentCard) -> None:
        self.cards[card.name] = card

    def render(self, exclude: str | None = None) -> str:
        return "\n".join(
            f"- {c.name} ({c.role}): {c.description}" for c in self.cards.values() if c.name != exclude
        )


async def register_persona(
    runtime, agent_cls: type["PersonaAgent"], card: AgentCard, directory: Directory, topics: tuple[str, ...] = (), **kwargs
) -> None:
    """Register an agent, subscribe it to the broadcast topic, its own inbox and any extra topics, and index it."""

    def factory() -> PersonaAgent:  # must take zero args
        return agent_cls(card=card, directory=directory, **kwargs)

    await agent_cls.register(runtime, card.name, factory)
    for topic in (BROADCAST_TOPIC, f"inbox.{card.name}", *topics):
        await runtime.add_subscription(TypeSubscription(topic_type=topic, agent_type=card.name))
    directory.add(card)


# --- The persona agent ----------------------------------------------------------------------------------


class PersonaAgent(RoutedAgent):
    def __init__(
        self,
        card: AgentCard,
        persona: str,
        model_client: ChatCompletionClient,
        directory: Directory,
        tools: list[Tool] = (),  # extra tools that do not need to know which agent calls them, e.g. web search
        max_hops: int = 4,
        max_tool_rounds: int = 6,
        history_size: int = 30,
    ) -> None:
        super().__init__(card.description)
        self._card = card
        self._persona = persona
        self._model = model_client
        self._directory = directory
        self._max_hops = max_hops
        self._max_tool_rounds = max_tool_rounds
        self._memory = ListMemory(name=f"{card.name}_notes")
        self._history = BufferedChatCompletionContext(buffer_size=history_size)
        # The runtime may run several handlers of one agent at once; process the inbox one message at a time.
        self._lock = asyncio.Lock()
        self._hops = 0
        self._tools = {
            t.name: t
            for t in [
                FunctionTool(self.send_message_to, name="send_message", description="Send a private message to another agent by name."),
                FunctionTool(self.list_agents, name="list_agents", description="List every agent in the simulation."),
                FunctionTool(self.remember, name="remember", description="Save a fact to your long-term notes."),
                *tools,
                *self.extra_tools(),
            ]
        }

    @property
    def card(self) -> AgentCard:
        return self._card

    # Extension points for scenario subclasses.

    def extra_tools(self) -> list[Tool]:
        return []

    def extra_context(self) -> str:
        """Appended to the system prompt on every LLM call."""
        return ""

    # Handlers: which one runs is chosen by the message's Python type.

    @message_handler
    async def on_direct(self, message: DirectMessage, ctx: MessageContext) -> None:
        await self.think(f"Private message from {message.sender}: {message.content}", ctx, hops=message.hops)

    @message_handler
    async def on_broadcast(self, message: Broadcast, ctx: MessageContext) -> None:
        await self.think(f"[BROADCAST from {message.sender}] {message.content}", ctx)

    # Tools (bound methods, so they act as this agent).

    async def send_message_to(self, to: str, message: str) -> str:
        if to not in self._directory.cards:
            return f"error: no agent named {to!r}. Known agents:\n{self._directory.render()}"
        if to == self._card.name:
            return "error: you cannot message yourself"
        if self._hops >= self._max_hops:
            return "error: conversation chain too long, wait for the next round"
        log(f"  {self._card.name} -> {to}: {message}")
        # Fire-and-forget publish to the recipient's inbox. runtime.send_message would be an RPC that blocks
        # the sender until the recipient's whole handler (and any chain it triggers) has finished.
        await self.publish_message(
            DirectMessage(self._card.name, to, message, hops=self._hops + 1), inbox_topic(to)
        )
        return "delivered"

    async def list_agents(self) -> str:
        return self._directory.render()

    async def remember(self, note: str) -> str:
        await self._memory.add(MemoryContent(content=note, mime_type=MemoryMimeType.TEXT))
        log(f"  {self._card.name} remembers: {note}")
        return "saved"

    # Checkpointing: runtime.save_state() / load_state() call these for every instantiated agent.

    async def save_state(self) -> dict:
        return {"history": await self._history.save_state(), "notes": [m.content for m in self._memory.content]}

    async def load_state(self, state: dict) -> None:
        await self._history.load_state(state["history"])
        self._memory.content = [MemoryContent(content=n, mime_type=MemoryMimeType.TEXT) for n in state["notes"]]

    # The think / tool-call loop.

    def _system_prompt(self) -> str:
        notes = "\n".join(f"- {m.content}" for m in self._memory.content) or "(none yet)"
        return (
            f"You are {self._card.name}, {self._card.role}.\n{self._persona}\n\n"
            f"Other agents you can message:\n{self._directory.render(exclude=self._card.name)}\n\n"
            f"Your notes:\n{notes}\n\n"
            "Other agents only see what you send with your messaging tools; your plain-text reply is a private "
            "thought that nobody else reads. Use remember for anything you need later. Be brief."
            f"{self.extra_context()}"
        )

    async def think(self, text: str, ctx: MessageContext, hops: int = 0) -> None:
        async with self._lock:
            self._hops = hops
            await self._history.add_message(UserMessage(content=text, source="inbox"))
            for _ in range(self._max_tool_rounds):
                history = await self._history.get_messages()
                # The buffer cuts at a fixed size; start at a user turn or Gemini rejects the request (400).
                while history and not isinstance(history[0], UserMessage):
                    history = history[1:]
                try:
                    result = await self._model.create(
                        [SystemMessage(content=self._system_prompt()), *history],
                        tools=list(self._tools.values()),
                        cancellation_token=ctx.cancellation_token,
                    )
                except Exception as e:  # one failed LLM call should not take the agent down for the whole run
                    log(f"  {self._card.name} LLM error: {type(e).__name__}: {str(e)[:200]}")
                    return
                await self._history.add_message(AssistantMessage(content=result.content, source=self._card.name))
                if isinstance(result.content, str):
                    log(f"  {self._card.name} (thought): {result.content}")
                    return
                outputs = [await self._run_tool(call, ctx) for call in result.content]
                await self._history.add_message(FunctionExecutionResultMessage(content=outputs))

    async def _run_tool(self, call: FunctionCall, ctx: MessageContext) -> FunctionExecutionResult:
        tool = self._tools.get(call.name)
        try:
            if tool is None:
                raise ValueError(f"unknown tool {call.name}")
            out = await tool.run_json(json.loads(call.arguments or "{}"), ctx.cancellation_token, call_id=call.id)
            return FunctionExecutionResult(content=tool.return_value_as_string(out), name=call.name, call_id=call.id)
        except Exception as e:  # report tool errors back to the model instead of crashing the agent
            return FunctionExecutionResult(content=f"error: {e}", name=call.name, call_id=call.id, is_error=True)


# --- Observability ------------------------------------------------------------------------------------------


class TranscriptRecorder(DefaultInterventionHandler):
    """Sees every message on the bus (the agents don't know) and appends one JSON line per message."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("a")

    def _write(self, kind: str, message: Any, sender: AgentId | None, to: str) -> None:
        data = dataclasses.asdict(message) if dataclasses.is_dataclass(message) else repr(message)
        event = {"t": datetime.now().isoformat(timespec="seconds"), "kind": kind, "type": type(message).__name__,
                 "sender": str(sender) if sender else None, "to": to, "data": data}
        self._file.write(json.dumps(event) + "\n")
        self._file.flush()

    async def on_publish(self, message: Any, *, message_context: MessageContext) -> Any:
        self._write("publish", message, message_context.sender, str(message_context.topic_id))
        return message

    async def on_send(self, message: Any, *, message_context: MessageContext, recipient: AgentId) -> Any:
        self._write("send", message, message_context.sender, str(recipient))
        return message


def log(line: str) -> None:
    print(line, flush=True)
