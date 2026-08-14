"""Google Antigravity CLI (`agy`) provider — Google-account auth via the CLI's
own login. Replaces the retired Gemini CLI. Text/JSON only; search and image
fall through the chain. The child env is stripped of GEMINI_API_KEY/
GOOGLE_API_KEY so a subscription run can never silently bill a metered key."""

import json
import os
import shutil
import subprocess

from app.config import settings
from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers.common import (
    RUN_TIMEOUT_SECONDS,
    extract_json,
    workdir,
)

NAME = "antigravity"
CAPABILITIES = {"text", "json"}


def available() -> bool:
    return shutil.which("agy") is not None


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    return env


def _run(system: str, user_content: str, extra_args: list[str]) -> dict:
    agy = shutil.which("agy")
    if agy is None:
        raise ClaudeError("agy CLI not found — install Antigravity and sign in once interactively")
    # Print mode has no separate system-prompt flag; prepend it. --sandbox and
    # --disable-slash-commands keep untrusted prompt content (job postings,
    # fetched pages) from reaching the terminal or expanding slash
    # commands/skills inside the run.
    argv = [
        agy,
        "-p",
        f"{system}\n\n{user_content}",
        "--output-format",
        "json",
        "--sandbox",
        "--disable-slash-commands",
        *extra_args,
    ]
    model = engine_prefs.get_model("antigravity") or settings.antigravity_model
    if model:
        argv += ["--model", model]
    proc = subprocess.run(
        argv, cwd=workdir(), env=_build_env(), capture_output=True, text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise ClaudeError(
            f"agy CLI exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
        )
    try:
        envelope = json.loads(proc.stdout)
    except ValueError as e:
        raise ClaudeError(f"agy CLI returned non-JSON output: {proc.stdout[-800:]}") from e
    if envelope.get("status") != "SUCCESS":
        raise ClaudeError(f"agy CLI run did not succeed: {proc.stdout[-800:]}")
    return envelope


def generate_text(system: str, user_content: str) -> str:
    return (_run(system, user_content, []).get("response") or "").strip()


def generate_json(system: str, user_content: str, schema: dict) -> dict:
    """--json-schema enforces the schema CLI-side; the envelope carries the
    parsed object in structured_output. Fall back to parsing the response
    text for agy builds that omit the field."""
    envelope = _run(system, user_content, ["--json-schema", json.dumps(schema)])
    out = envelope.get("structured_output")
    if isinstance(out, dict):
        return out
    try:
        return extract_json(envelope.get("response") or "")
    except (ValueError, json.JSONDecodeError) as e:
        raise ClaudeError(f"agy CLI returned no usable JSON: {str(envelope)[-800:]}") from e
