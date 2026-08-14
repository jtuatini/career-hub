"""Brainstorm: headless chat sessions over the brain, on the selected engine.

Each message spawns a subscription-billed headless CLI run in the same sandbox
as the terminal. Claude sessions get the copilot MCP read tools and --resume
threading; codex (`codex exec` + thread resume) and antigravity (`agy -p` +
--conversation) thread natively but run without the MCP mounts; the custom
engine is stateless per message. Saving ideas into the brain is deliberately
NOT a session tool — the UI's save button writes through /api/memory so the
user approves every save.
"""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from app.services import engine_prefs
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

# codex/antigravity/custom brainstorm runs have NO copilot MCP tools, so the
# persona must not promise brain tools it doesn't have. This is an upstream
# limitation, not an oversight: headless `codex exec` auto-cancels every MCP
# tool call ("user cancelled MCP tool call" — openai/codex#16685/#24135) and
# the only bypass also disables the shell sandbox, which prompt-exposed runs
# must never do; agy print mode has the same approval wall. Interactive
# Terminal sessions DO get the tools — their TUIs let the user approve calls.
PERSONA_GENERIC = """\
You are the user's brainstorming partner inside their Application Copilot app, talking
through internship applications: which experiences to highlight, angles for essays
and answers, companies and roles worth pursuing, interview stories.

You have NO access to their saved memories or resume bank in this session — never
invent experiences they don't have. Ask about their real material and build on what
they tell you. Be a sparring partner: concrete options over platitudes, push back
when an idea is weak, keep responses tight.\
"""


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


async def _run_lines(argv: list[str], env: dict | None) -> tuple[list[str], int, str]:
    """Run a headless CLI turn; returns (stdout lines, returncode, stderr tail)."""
    ws = terminal_service.ensure_workspace()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=ws, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(SESSION_TIMEOUT_SECONDS):
            stdout, stderr = await proc.communicate()
    except TimeoutError:
        proc.kill()
        raise
    return (
        stdout.decode("utf-8", "replace").splitlines(),
        proc.returncode or 0,
        stderr.decode("utf-8", "replace").strip()[-2000:],
    )


async def _antigravity_reply(message: str, session_id: str | None) -> AsyncIterator[dict]:
    agy = shutil.which("agy")
    if agy is None:
        yield {"type": "error", "message": "agy CLI not found — install Antigravity and sign in once"}
        return
    prompt = message if session_id else f"{PERSONA_GENERIC}\n\n{message}"
    argv = [agy, "-p", prompt, "--output-format", "json", "--sandbox", "--disable-slash-commands"]
    if session_id:
        argv += ["--conversation", session_id]
    if model := engine_prefs.get_model("antigravity"):
        argv += ["--model", model]
    try:
        lines, code, stderr = await _run_lines(argv, terminal_service.build_env(Path(agy)))
    except TimeoutError:
        yield {"type": "error", "message": f"agy run timed out after {SESSION_TIMEOUT_SECONDS}s"}
        return
    try:
        envelope = json.loads("\n".join(lines))
    except ValueError:
        yield {"type": "error", "message": stderr or f"agy exited {code} with non-JSON output"}
        return
    if code != 0 or envelope.get("status") != "SUCCESS":
        yield {"type": "error", "message": stderr or f"agy run failed: {envelope.get('status')}"}
        return
    sid = envelope.get("conversation_id")
    yield {"type": "session", "session_id": sid}
    yield {"type": "text", "text": (envelope.get("response") or "").strip()}
    yield {"type": "done", "session_id": sid, "is_error": False}


async def _codex_reply(message: str, session_id: str | None) -> AsyncIterator[dict]:
    codex = shutil.which("codex")
    if codex is None:
        yield {"type": "error", "message": "codex CLI not found — install it and run `codex login`"}
        return
    common = ["--json", "--skip-git-repo-check", "-s", "read-only"]
    if model := engine_prefs.get_model("codex"):
        common += ["-m", model]
    if session_id:
        argv = [codex, "exec", "resume", session_id, *common, message]
    else:
        argv = [codex, "exec", *common, f"{PERSONA_GENERIC}\n\n{message}"]
    try:
        lines, code, stderr = await _run_lines(argv, terminal_service.build_env(Path(codex)))
    except TimeoutError:
        yield {"type": "error", "message": f"codex run timed out after {SESSION_TIMEOUT_SECONDS}s"}
        return
    sid, texts = session_id, []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "thread.started":
            sid = event.get("thread_id")
        elif event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                texts.append(item["text"])
            elif item.get("type") == "command_execution":
                yield {"type": "tool", "name": "shell"}
    if code != 0 and not texts:
        yield {"type": "error", "message": stderr or f"codex exited {code}"}
        return
    yield {"type": "session", "session_id": sid}
    yield {"type": "text", "text": "\n\n".join(texts).strip()}
    yield {"type": "done", "session_id": sid, "is_error": False}


async def _custom_reply(message: str) -> AsyncIterator[dict]:
    from app.services.engine_providers import custom_cli

    try:
        text = await asyncio.to_thread(custom_cli.generate_text, PERSONA_GENERIC, message)
    except Exception as e:  # ClaudeError or subprocess timeout
        yield {"type": "error", "message": str(e)}
        return
    yield {"type": "text", "text": text}
    # No session event: the custom template has no conversation threading —
    # each turn stands alone.
    yield {"type": "done", "session_id": None, "is_error": False}


async def stream_reply(message: str, session_id: str | None) -> AsyncIterator[dict]:
    """Run one brainstorm turn on the selected engine; yields
    session/text/tool/done/error event dicts."""
    if BRAINSTORM_ARGV is None:
        provider = engine_prefs.get_provider()
        if provider == "antigravity":
            async for event in _antigravity_reply(message, session_id):
                yield event
            return
        if provider == "codex":
            async for event in _codex_reply(message, session_id):
                yield event
            return
        if provider == "custom":
            async for event in _custom_reply(message):
                yield event
            return
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
