"""Brainstorm: headless `claude -p` chat sessions over the brain.

Each message spawns a subscription-billed headless Claude Code run in the same
sandbox as the terminal, with read-only brain tools mounted; --resume threads the
conversation. Saving ideas into the brain is deliberately NOT a session tool —
the UI's save button writes through /api/memory so the user approves every save.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from app.services import terminal as terminal_service

# Tests override this with a stub that prints canned stream-json (message appended).
BRAINSTORM_ARGV: list[str] | None = None

SESSION_TIMEOUT_SECONDS = 300

PERSONA = """\
You are the user's brainstorming partner inside their Application Copilot app, talking
through internship applications: which experiences to highlight, angles for essays
and answers, companies and roles worth pursuing, interview stories.

Ground everything in his real material: use search_memory and search_qa before
proposing ideas, and never invent experiences he doesn't have. If the brain lacks
material on a topic, say so and ask him about it — his answers become brain entries
he can save from the UI. Be a sparring partner: concrete options over platitudes,
push back when an idea is weak, keep responses tight.

Your complete toolset: the copilot MCP tools (search_memory, get_entry, search_qa,
list_jobs, get_job) plus WebSearch and WebFetch. You have no file, Bash, or write
access — do not attempt other tools.\
"""

READ_TOOLS = (
    "WebSearch,WebFetch,"
    "mcp__copilot__search_memory,mcp__copilot__get_entry,mcp__copilot__search_qa,"
    "mcp__copilot__list_jobs,mcp__copilot__get_job"
)


def build_argv(message: str, session_id: str | None) -> list[str]:
    if BRAINSTORM_ARGV is not None:
        return [*BRAINSTORM_ARGV, message]
    ws = terminal_service.ensure_workspace()
    argv = [
        str(terminal_service.find_claude_binary()),
        "-p",
        message,
        "--output-format",
        "stream-json",
        "--verbose",
        "--append-system-prompt",
        PERSONA,
        "--mcp-config",
        str(ws / ".mcp.json"),
        "--strict-mcp-config",
        "--allowedTools",
        READ_TOOLS,
        "--max-turns",
        "10",
    ]
    if session_id:
        argv += ["--resume", session_id]
    return argv


def _events_from_line(line: str) -> list[dict]:
    """Translate one claude stream-json line into UI events."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        return [{"type": "session", "session_id": event.get("session_id")}]
    if kind == "assistant":
        out = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text"):
                out.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "tool_use":
                out.append({"type": "tool", "name": block.get("name", "?")})
        return out
    if kind == "result":
        return [
            {
                "type": "done",
                "session_id": event.get("session_id"),
                "is_error": bool(event.get("is_error")),
            }
        ]
    return []


async def stream_reply(message: str, session_id: str | None) -> AsyncIterator[dict]:
    """Run one brainstorm turn; yields session/text/tool/done/error event dicts."""
    ws = terminal_service.ensure_workspace()
    try:
        argv = build_argv(message, session_id)
    except FileNotFoundError as e:
        yield {"type": "error", "message": str(e)}
        return
    env = None
    if BRAINSTORM_ARGV is None:
        env = terminal_service.build_env(terminal_service.find_claude_binary())

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=ws,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    got_done = False
    try:
        async with asyncio.timeout(SESSION_TIMEOUT_SECONDS):
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                for event in _events_from_line(line.decode("utf-8", "replace")):
                    got_done = got_done or event["type"] == "done"
                    yield event
            await proc.wait()
    except TimeoutError:
        proc.kill()
        yield {"type": "error", "message": f"Brainstorm run timed out after {SESSION_TIMEOUT_SECONDS}s"}
        return
    if proc.returncode != 0 and not got_done:
        stderr = (await proc.stderr.read()).decode("utf-8", "replace").strip() if proc.stderr else ""
        yield {"type": "error", "message": stderr[-2000:] or f"claude exited {proc.returncode}"}
