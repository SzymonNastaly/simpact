"""Generic persona agents on Concordia: an entity with a persona, an index of the others, notes it keeps, and tools.

Concordia supplies the entity/component system, the game master machinery, the engines and the Gemini wrapper.
What is ours: a tool adapter, a notes component, an act component that lets the LLM call tools before it answers,
and the PersonaAgent prefab that wires them together. Scenarios (e.g. model_un.py) add a game master that sets the
rules: who acts when, what they may do, and what their actions cause.
"""

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import tenacity
from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import agent as agent_components
from concordia.document import interactive_document_tools, tool
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from concordia.typing import prefab as prefab_lib

OBSERVATION_KEY = agent_components.observation.DEFAULT_OBSERVATION_COMPONENT_KEY
MEMORY_KEY = agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY


def log(line: str) -> None:
    print(line, flush=True)


def no_embedding(text: str) -> np.ndarray:
    """Sentence embedder for Concordia's memory banks. Nothing here retrieves memories by similarity, only by recency,
    so a constant vector will do. Plug in a real embedder if you add a component such as AllSimilarMemories."""
    return np.ones(3)


class FunctionTool(tool.Tool):
    """A Python function as a Concordia tool. The description is all the LLM sees, so it must name the arguments."""

    def __init__(self, fn: Callable[..., str], name: str, description: str) -> None:
        self._fn = fn
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    def execute(self, **kwargs: Any) -> str:
        return str(self._fn(**kwargs))


class Notes(agent_components.action_spec_ignored.ActionSpecIgnored):
    """Long-term notes the entity writes with its `remember` tool. All of them go into every prompt."""

    def __init__(self, pre_act_label: str = "Your notes") -> None:
        super().__init__(pre_act_label)
        self._notes: list[str] = []

    def _make_pre_act_value(self) -> str:
        return "\n".join(f"- {note}" for note in self._notes) or "(none yet)"

    def remember(self, note: str) -> str:
        self._notes.append(note)
        return "saved"

    def get_state(self) -> entity_component.ComponentState:
        return {"notes": list(self._notes)}

    def set_state(self, state: entity_component.ComponentState) -> None:
        self._notes = list(state["notes"])


class ToolUseAct(entity_component.ActingComponent, entity_component.ComponentWithLogging):
    """Answers the game master's call to action. Before answering, the LLM may call tools (Concordia's
    InteractiveDocumentWithTools runs that loop and records each call and result in the prompt).

    The action comes back on one line, "<name>: <answer>": the simultaneous engine joins a round's actions with
    newlines, and one line per entity lets the game master split them again.
    """

    def __init__(
        self,
        model: language_model.LanguageModel,
        tools: Sequence[tool.Tool],
        component_order: Sequence[str],
        max_tool_calls: int = 5,
    ) -> None:
        super().__init__()
        self._model = model
        self._tools = list(tools)
        self._component_order = list(component_order)
        self._max_tool_calls = max_tool_calls

    def get_action_attempt(
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        if action_spec.output_type != entity_lib.OutputType.FREE:
            raise NotImplementedError(f"ToolUseAct only answers free-text action specs, not {action_spec.output_type}")
        name = self.get_entity().name
        prompt = interactive_document_tools.InteractiveDocumentWithTools(
            self._model, tools=self._tools, max_tool_calls_per_question=self._max_tool_calls,
            max_tool_result_length=2000,
        )
        prompt.statement("\n".join(contexts[key] for key in self._component_order if contexts[key]))
        try:
            # Generous max_tokens: Gemini counts its thinking tokens against it.
            answer = prompt.open_question(
                action_spec.call_to_action.replace("{name}", name),
                max_tokens=16_000, terminators=(), question_label="Exercise",
            )
        except Exception as e:  # one failed LLM call should not take the agent down for the whole run
            if isinstance(e, tenacity.RetryError):
                e = e.last_attempt.exception()
            log(f"  {name} LLM error: {type(e).__name__}: {str(e)[:200]}")
            answer = ""
        for content in prompt.contents():
            if interactive_document_tools.TOOL_CALL_TAG in content.tags:
                log(f"  {name} {content.text.strip()}")
        self._logging_channel({"Summary": f"Action: {answer}", "Value": answer,
                               "Prompt": prompt.view().text().splitlines()})
        return f"{name}: {' '.join(answer.split())}"

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        pass


@dataclasses.dataclass
class PersonaAgent(prefab_lib.Prefab):
    """Prefab for an entity with a private persona, an index of the others, notes, recent observations and tools."""

    description: str = (
        "An entity with a private persona, an index of the other entities, notes it writes itself with a remember "
        "tool, its most recent observations, and any extra tools."
    )
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "Alice",  # letters, digits and underscores; other entities address it by this name
            "role": "a negotiator",
            "persona": "",  # private: objectives, constraints, style
            "context": "",  # the shared situation, e.g. committee and agenda
            "directory": [],  # [{"name", "role", "description"}] for every entity; descriptions are public
            "tools": [],  # tools that don't need to know who calls them, e.g. web search
            "history_length": 50,  # observations kept in the prompt
            "max_tool_calls": 5,  # per turn, before the entity must answer
        }
    )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        p = self.params
        name = p["name"]
        others = "\n".join(
            f"- {c['name']} ({c['role']}): {c['description']}" for c in p.get("directory", []) if c["name"] != name
        )
        notes = Notes()
        components = {
            "Instructions": agent_components.constant.Constant(
                state=(
                    f"You are {name}, {p.get('role', 'a negotiator')}.\n{p.get('persona', '')}\n\n"
                    f"{p.get('context', '')}\n\n"
                    "Others only learn what you do through your moves; everything else you write stays private. "
                    "Use the remember tool for anything you need later. Be brief."
                ),
                pre_act_label="Instructions",
            ),
            "Directory": agent_components.constant.Constant(state=others, pre_act_label="Others you can message"),
            "Notes": notes,
            OBSERVATION_KEY: agent_components.observation.LastNObservations(
                history_length=p.get("history_length", 50),
                pre_act_label="What you have seen and heard (oldest first)",
            ),
            "observation_to_memory": agent_components.observation.ObservationToMemory(),
            MEMORY_KEY: agent_components.memory.AssociativeMemory(memory_bank=memory_bank),
        }
        remember = FunctionTool(notes.remember, "remember", "Save a fact to your long-term notes. Args: note (str)")
        act = ToolUseAct(
            model,
            tools=[*p.get("tools", []), remember],
            component_order=["Instructions", "Directory", "Notes", OBSERVATION_KEY],
            max_tool_calls=p.get("max_tool_calls", 5),
        )
        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name, act_component=act, context_components=components
        )
