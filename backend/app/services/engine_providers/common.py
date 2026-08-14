"""Shared plumbing for CLI engine providers: JSON coercion with one retry,
timeouts, and the sandbox working directory for headless runs."""

import json
import re
from pathlib import Path

from app.config import settings

RUN_TIMEOUT_SECONDS = 240
SEARCH_TIMEOUT_SECONDS = 420

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def json_instruction(schema: dict) -> str:
    return (
        "\n\nReply with ONLY a single JSON object matching this JSON schema — "
        f"no prose, no code fences, no explanations:\n{json.dumps(schema)}"
    )


def extract_json(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", text.strip())
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(cleaned[start : end + 1])


def generate_json_via_text(run, system: str, user_content: str, schema: dict) -> dict:
    """run(system, user_content) -> str. One retry on non-JSON replies."""
    system_full = system + json_instruction(schema)
    reply = run(system_full, user_content)
    try:
        return extract_json(reply)
    except (ValueError, json.JSONDecodeError):
        retry = run(
            system_full,
            user_content
            + "\n\nIMPORTANT: your previous reply was not valid JSON. "
            "Reply with ONLY the JSON object.",
        )
        return extract_json(retry)


def workdir() -> Path:
    ws = settings.data_dir / "ai-workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws
