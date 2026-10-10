"""Gemini for AutoGen: a chat client (OpenAI-compatible endpoint) and a web search tool (Google Search grounding)."""

import os
from functools import cache

from autogen_core.models import ModelInfo
from autogen_ext.models.openai import OpenAIChatCompletionClient
from google import genai
from google.genai import types


def default_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-flash-latest")


def gemini_client(model: str | None = None) -> OpenAIChatCompletionClient:
    client = OpenAIChatCompletionClient(
        model=model or default_model(),
        api_key=os.environ["GEMINI_API_KEY"],
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        # Needed for model names newer than autogen-ext's built-in table (it stops at gemini-2.5).
        model_info=ModelInfo(vision=False, function_calling=True, json_output=True,
                             structured_output=True, family="unknown", multiple_system_messages=False),
    )
    _preserve_thought_signatures(client)
    return client


def _preserve_thought_signatures(client: OpenAIChatCompletionClient) -> None:
    """Gemini 3+ attaches a thought signature to tool calls (tool_calls[i].extra_content.google.thought_signature)
    and rejects the next request with a 400 if it is not sent back. autogen-ext 0.7.5 predates this and drops
    the field, so remember it per tool call and re-attach it to outgoing assistant messages."""
    signatures: dict[tuple[str, str, str], dict] = {}
    completions = client._client.chat.completions  # the AsyncOpenAI resource autogen calls
    original_create = completions.create

    def key(call_id: str, name: str, arguments: str) -> tuple[str, str, str]:
        return (call_id, name, arguments)

    async def create(*args, **kwargs):
        for msg in kwargs.get("messages", []):
            for call in msg.get("tool_calls") or []:
                fn = call["function"]
                if (extra := signatures.get(key(call["id"], fn["name"], fn["arguments"]))) is not None:
                    call["extra_content"] = extra
        response = await original_create(*args, **kwargs)
        for choice in response.choices:
            for call in choice.message.tool_calls or []:
                if extra := (call.model_extra or {}).get("extra_content"):
                    signatures[key(call.id, call.function.name, call.function.arguments)] = extra
        return response

    completions.create = create


@cache
def _genai() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


async def web_search(query: str) -> str:
    """Search the web and return a short summary of what the top results say, with source links."""
    response = await _genai().aio.models.generate_content(
        model=default_model(),
        contents=f"Search the web and summarise the key facts and figures in under 150 words: {query}",
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    chunks = (response.candidates[0].grounding_metadata.grounding_chunks or []) if response.candidates else []
    sources = ", ".join(c.web.title for c in chunks[:5] if c.web and c.web.title)  # uris are long redirect links
    return f"{response.text}\n\nSources: {sources or '(none returned)'}"
