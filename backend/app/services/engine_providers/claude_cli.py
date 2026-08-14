"""Claude Code CLI provider — the original subscription engine, extracted
verbatim from engine.py. Auth is the user's Claude subscription; the child env
is stripped of ANTHROPIC_API_KEY (terminal.build_env) so a subscription run
can never silently bill the metered API."""

import json
import re
import subprocess
import uuid

from app.config import settings
from app.services import engine_prefs, terminal as terminal_service
from app.services.claude import ClaudeError
from app.services.engine_providers.common import (
    RUN_TIMEOUT_SECONDS,
    SEARCH_TIMEOUT_SECONDS,
    extract_json,
    generate_json_via_text,
    json_instruction,
)

NAME = "claude"
CAPABILITIES = {"text", "json", "search", "image"}


def available() -> bool:
    try:
        terminal_service.find_claude_binary()
        return True
    except FileNotFoundError:
        return False


def generate_text(system: str, user_content: str) -> str:
    return _run_cli(system, user_content).strip()


def generate_json(system: str, user_content: str, schema: dict) -> dict:
    return generate_json_via_text(_run_cli, system, user_content, schema)


def search(system: str, user_content: str) -> tuple[str, list[str]]:
    return _run_cli_search(system, user_content)


def image(system: str, png_bytes: bytes, schema: dict) -> dict:
    return _cli_generate_json_with_image(system, png_bytes, schema)


_GENERATION_DISALLOWED = (
    "TodoWrite",
    "Task",
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)


def _run_cli(system: str, user_content: str, allowed_tools: str = "") -> str:
    """One headless claude run; returns the result text from the JSON envelope."""
    claude_bin = terminal_service.find_claude_binary()
    ws = terminal_service.ensure_workspace()
    # --append-system-prompt ADDS to Claude Code's agent persona, so open-ended
    # writing tasks ("draft a cover letter") otherwise trigger multi-turn agent
    # behavior (todo bookkeeping etc.) until --max-turns kills the run. Pin it
    # to a single direct reply and disallow the agentic built-ins outright.
    single_reply = (
        "Reply with the requested output directly in a single message. "
        "Do not use any tools, do not create todo lists, do not plan steps.\n\n"
    )
    if allowed_tools:
        single_reply = (
            f"Reply with the requested output in a single message. You may use only "
            f"these tools: {allowed_tools}. Do not create todo lists, do not plan steps.\n\n"
        )
    # Deny rules beat allow rules in the CLI, so anything explicitly allowed
    # (the image path needs Read) must drop out of the disallowed list.
    allowed = {t.strip() for t in allowed_tools.split(",") if t.strip()}
    disallowed = ",".join(t for t in _GENERATION_DISALLOWED if t not in allowed)
    argv = [
        str(claude_bin),
        "-p",
        user_content,
        "--append-system-prompt",
        single_reply + system,
        "--disallowedTools",
        disallowed,
        "--output-format",
        "json",
        "--model",
        engine_prefs.get_model("claude") or settings.claude_model,
        # Generation-only runs have no tools, but long outputs (voice-profile
        # cover letters) can span several CLI turns; 3 was hit in practice.
        "--max-turns",
        "10",
        "--allowedTools",
        allowed_tools,
        "--strict-mcp-config",
    ]
    return _exec_cli(argv, ws, claude_bin, RUN_TIMEOUT_SECONDS)


def _exec_cli(argv: list[str], ws, claude_bin, timeout: int) -> str:
    """Run the CLI and unwrap its JSON envelope. Shared by generation and
    search runs so both get identical error handling and key-stripped env."""
    proc = subprocess.run(
        argv,
        cwd=ws,
        env=terminal_service.build_env(claude_bin),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ClaudeError(
            f"claude CLI exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
        )
    envelope = json.loads(proc.stdout)
    if envelope.get("is_error"):
        raise ClaudeError(f"claude CLI reported an error: {str(envelope.get('result'))[:800]}")
    return envelope.get("result") or ""


# Trailing punctuation that commonly rides along when a URL ends a sentence or
# sits inside markdown parens/brackets.
_URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
_URL_TRAILING = ".,;:!?)]}\"'*"


def _extract_urls(text: str) -> list[str]:
    """Source list for CLI search runs: the API path gets structured citations,
    the CLI reports its sources inline, so harvest them from the reply."""
    seen: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(_URL_TRAILING)
        if url and url not in seen:
            seen.append(url)
    return seen


def _run_cli_search(system: str, user_content: str) -> tuple[str, list[str]]:
    """Headless run WITH the CLI's own WebSearch/WebFetch tools — the
    subscription equivalent of the API's server-side web_search."""
    claude_bin = terminal_service.find_claude_binary()
    ws = terminal_service.ensure_workspace()
    # Unlike generation runs (pinned to a single tool-free reply), a search run
    # must be allowed to browse before answering — but still no todo/plan
    # theatre, and no filesystem or shell access.
    search_preamble = (
        "Use the WebSearch tool to research this, then give the requested output "
        "in a single final message. Cite the source URLs you used inline. "
        "Do not create todo lists, do not plan steps.\n\n"
    )
    argv = [
        str(claude_bin),
        "-p",
        user_content,
        "--append-system-prompt",
        search_preamble + system,
        "--disallowedTools",
        "TodoWrite,Task,Bash,Read,Write,Edit,Glob,Grep,NotebookEdit",
        "--allowedTools",
        "WebSearch,WebFetch",
        "--output-format",
        "json",
        "--model",
        engine_prefs.get_model("claude") or settings.claude_model,
        # Each search + read is a turn; browsing several sources needs headroom.
        "--max-turns",
        "24",
        "--strict-mcp-config",
    ]
    text = _exec_cli(argv, ws, claude_bin, SEARCH_TIMEOUT_SECONDS)
    return text, _extract_urls(text)


def _cli_generate_json(system: str, user_content: str, schema: dict) -> dict:
    return generate_json_via_text(_run_cli, system, user_content, schema)


def _cli_generate_json_with_image(system: str, png_bytes: bytes, schema: dict) -> dict:
    ws = terminal_service.ensure_workspace()
    image_dir = ws / ".parse"
    image_dir.mkdir(exist_ok=True)
    image_path = image_dir / f"{uuid.uuid4().hex}.png"
    image_path.write_bytes(png_bytes)
    try:
        system_full = system + json_instruction(schema)
        reply = _run_cli(
            system_full,
            f"Read the screenshot at {image_path} and answer based on it.",
            allowed_tools="Read",
        )
        return extract_json(reply)
    finally:
        image_path.unlink(missing_ok=True)
