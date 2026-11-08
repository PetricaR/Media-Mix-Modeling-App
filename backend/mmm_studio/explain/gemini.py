"""The Gemini call behind the explanations (Google GenAI SDK, Gemini API key or Vertex AI).

`google.genai` is imported lazily, so importing the API does not load it until the first explanation.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from mmm_studio.errors import public_error
from mmm_studio.settings import Settings

logger = logging.getLogger(__name__)


class ExplainUnavailable(RuntimeError):
    """Gemini is not configured on this server."""


class ExplainFailed(RuntimeError):
    """Gemini was configured but gave no usable answer; the message is safe to show."""


@lru_cache(maxsize=4)
def _client(api_key: str, vertex: bool, project: str, location: str, timeout_ms: int) -> Any:
    from google import genai
    from google.genai import types

    http = types.HttpOptions(timeout=timeout_ms)
    if vertex:
        return genai.Client(vertexai=True, project=project, location=location, http_options=http)
    return genai.Client(api_key=api_key, http_options=http)


def _thinking(model: str, level: str = "low") -> Any:
    """Thinking config. LOW keeps answers fast: chart/table explanations within seconds, and a full settings review
    (about 50 decisions) within about a minute; MEDIUM ran past the deadline on the latter."""
    from google.genai import types

    name = model.lower()
    if name.startswith("gemini-2.5-flash"):
        return types.ThinkingConfig(thinking_budget=0 if level == "low" else -1)  # -1: dynamic
    if name.startswith("gemini-2.5-pro"):
        # 128 is the minimum; Pro cannot switch thinking off
        return types.ThinkingConfig(thinking_budget=128 if level == "low" else -1)
    if name.startswith(("gemini-3", "gemini-4")):
        # LOW is the lowest level Gemini 4 Argon accepts (MINIMAL and thinking_budget are a 400); its default is MEDIUM
        return types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.LOW if level == "low" else types.ThinkingLevel.MEDIUM
        )
    return None  # Gemini 2.0 and older reject a thinking config


def _api_error_message(code: int | None, status: str | None, message: str | None, settings: Settings) -> str:
    detail = (message or "").strip().splitlines()[0][:200] if message else ""
    if code in (401, 403):
        how = (
            "the service account needs the Vertex AI User role"
            if settings.gemini_use_vertex
            else "check MMM_GEMINI_API_KEY / GEMINI_API_KEY"
        )
        return f"Gemini refused the credentials ({code}): {how}."
    if code == 404:
        where = f" in location '{settings.gemini_location}'" if settings.gemini_use_vertex else ""
        return f"The Gemini model '{settings.gemini_model}' is not available{where}."
    if code == 429:
        return "The Gemini quota is used up for now. Try again in a minute."
    if code is not None and code >= 500:
        return f"Gemini is temporarily unavailable ({code}). Try again shortly."
    return f"Gemini rejected the request ({code} {status or ''}): {detail}".strip()


def _generate(
    system: str,
    prompt: str,
    settings: Settings,
    *,
    thinking: str = "low",
    schema: Any = None,
    timeout_seconds: int | None = None,
) -> str:
    """Gemini's answer text, or `ExplainUnavailable` / `ExplainFailed` with a reason in plain words."""
    if not settings.explain_available:
        raise ExplainUnavailable(
            "Gemini is not configured on this server: set MMM_GEMINI_API_KEY, or MMM_GEMINI_VERTEX=true with a GCP project."
        )
    from google.genai import errors, types

    client = _client(
        settings.gemini_api_key.strip(),
        settings.gemini_use_vertex,
        settings.gcp_project_id.strip(),
        settings.gemini_location,
        (timeout_seconds or settings.gemini_timeout_seconds) * 1000,
    )
    extra: dict[str, Any] = {}
    if schema is not None:
        extra = {"response_mime_type": "application/json", "response_schema": schema}
    config = types.GenerateContentConfig(
        system_instruction=system, thinking_config=_thinking(settings.gemini_model, thinking), **extra
    )
    try:
        response = client.models.generate_content(model=settings.gemini_model, contents=prompt, config=config)
    except errors.APIError as exc:
        logger.warning("Gemini API error %s %s: %s", exc.code, exc.status, exc.message)
        raise ExplainFailed(_api_error_message(exc.code, exc.status, exc.message, settings)) from exc
    except Exception as exc:  # network, timeout or credential errors raised below the SDK
        logger.warning("Gemini call failed: %s", public_error(exc))
        raise ExplainFailed(f"Gemini could not be reached ({public_error(exc)}).") from exc
    text = (response.text or "").strip()
    if not text:
        raise ExplainFailed("Gemini returned no text for this item (the answer may have been blocked). Try again.")
    return text


def generate_explanation(system: str, prompt: str, settings: Settings) -> str:
    """Gemini's Markdown answer for `prompt`, or `ExplainUnavailable` / `ExplainFailed` with a reason in plain words."""
    return _generate(system, prompt, settings)


def generate_json(system: str, prompt: str, settings: Settings, schema: Any) -> str:
    """Gemini's JSON answer for `prompt`, constrained to the pydantic `schema`, with the recommendation timeout."""
    return _generate(system, prompt, settings, schema=schema, timeout_seconds=settings.gemini_recommend_timeout_seconds)
