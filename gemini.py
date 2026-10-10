"""Gemini for Concordia: the language model every entity uses, and a web search tool (Google Search grounding)."""

import os
from functools import cache

from concordia.contrib.language_models.google import gemini_model as concordia_gemini
from concordia.language_model import language_model, retry_wrapper
from google import genai
from google.genai import types

MODEL = "gemini-3.8-flash"


def gemini_model() -> language_model.LanguageModel:
    # Concordia's own Gemini wrapper (google-genai SDK, reads GEMINI_API_KEY). Long runs make hundreds of calls,
    # so retry with backoff instead of losing a delegation's turn to one 429 or 503.
    return retry_wrapper.RetryLanguageModel(concordia_gemini.GeminiModel(MODEL))


@cache
def _genai() -> genai.Client:
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def web_search(query: str) -> str:
    """Search the web and return a short summary of what the top results say, with source names."""
    response = _genai().models.generate_content(
        model=MODEL,
        contents=f"Search the web and summarise the key facts and figures in under 150 words: {query}",
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    )
    metadata = response.candidates[0].grounding_metadata if response.candidates else None
    chunks = (metadata.grounding_chunks if metadata else None) or []
    sources = ", ".join(c.web.title for c in chunks[:5] if c.web and c.web.title)  # uris are long redirect links
    return f"{response.text}\n\nSources: {sources or '(none returned)'}"
