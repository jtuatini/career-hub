"""Embedded Claude Code terminal: PTY session management + sandbox provisioning.

One interactive `claude` session at a time, running in data/ai-workspace/ — a sandbox
with a generated CLAUDE.md and the copilot MCP server mounted. The session belongs to
the backend, not the websocket: it survives disconnects, buffers recent output for
replay, and is only torn down by an explicit restart or process exit.
"""

import asyncio
import fcntl
import json
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios
import threading
from pathlib import Path

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
BUFFER_LIMIT = 200_000  # bytes of scrollback replayed on reattach

# Tests override this with a stub command (e.g. ["/bin/cat"]).
SESSION_ARGV: list[str] | None = None

WORKSPACE_CLAUDE_MD = """\
# AI Workspace — Application Copilot

You are running inside the Application Copilot app, the user's local internship
application system. This folder is your sandbox: keep scratch files here. The app's
own source code is out of scope — work through the tools below instead.

## What you can do

- **Resume bank + job tracker** via the `copilot` MCP tools: `list_resumes`,
  `get_resume_tex`, `update_resume_tex`, `bulk_find_replace`, `list_jobs`, `get_job`.
  Every edit creates a new compiled version; compile errors come back to you — fix
  the LaTeX and retry.
- **The brain** — the user's memory web of real experiences, skills, stories, and past
  application answers: `search_memory`, `get_entry`, `add_entry`, `link_entries`,
  `search_qa`, `save_qa_answer`. Ground resume wording and answers in what the brain
  actually contains; never invent experiences. Save new material only when the user
  states or approves it.
- **Web** via WebSearch/WebFetch: postings, companies, deadlines.
- The app's HTTP API runs at http://127.0.0.1:8321 (OpenAPI: /api/openapi.json).
  It requires an `X-Copilot-Token` header; the token lives in `../api_token`
  relative to this workspace (reading it will prompt for permission). Prefer the
  MCP tools — they need no token.

## Rules

- Resumes are LaTeX templates. Preserve structure, commands, and preamble — edit
  wording only, unless the user explicitly asks for structural changes.
- Resumes must stay one page: check `page_count` on the new version after editing.
- Edit only entries with `is_latest_version: true`; `has_tex: false` entries are
  PDF-only and cannot be edited.
- Never delete resumes or jobs unless the user explicitly asks.
"""


def find_claude_binary() -> Path:
    found = shutil.which("claude")
    if found:
        return Path(found)
    fallback = Path.home() / ".local" / "bin" / "claude"
    if fallback.exists():
        return fallback
    raise FileNotFoundError(
        "claude CLI not found — install Claude Code or put `claude` on the backend's PATH"
    )


def _find_uv_binary() -> Path:
    found = shutil.which("uv")
    if found:
        return Path(found)
    fallback = Path.home() / ".local" / "bin" / "uv"
    if fallback.exists():
        return fallback
    raise FileNotFoundError("uv not found — the MCP server is launched via uv")


def ensure_workspace() -> Path:
    """Provision data/ai-workspace/. Regenerated on every spawn so the guidance
    and MCP wiring always match the current code."""
    ws = settings.data_dir / "ai-workspace"
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    (ws / "CLAUDE.md").write_text(WORKSPACE_CLAUDE_MD)
    mcp_config = {
        "mcpServers": {
            "copilot": {
                "command": str(_find_uv_binary()),
                # --directory (not --project): the backend isn't an installed package,
                # so app.* is only importable with backend/ as the working directory.
                "args": ["run", "--directory", str(BACKEND_DIR), "python", "-m", "app.mcp_server"],
            }
        }
    }
    (ws / ".mcp.json").write_text(json.dumps(mcp_config, indent=2))
    (ws / ".claude" / "settings.json").write_text(
        json.dumps({"enableAllProjectMcpServers": True}, indent=2)
    )
    return ws


def build_env(claude_bin: Path) -> dict[str, str]:
    env = dict(os.environ)
    # Subscription auth only: with the key in the environment, claude would silently
    # bill the metered API instead of the user's plan.
    env.pop("ANTHROPIC_API_KEY", None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["PATH"] = f"{claude_bin.parent}:{env.get('PATH', '')}"
    return env


class TerminalSession:
    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.loop = asyncio.get_running_loop()
        self.master_fd, slave_fd = pty.openpty()
        self.proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        self.alive = True
        self.buffer = bytearray()
        self.subscriber: asyncio.Queue[bytes] | None = None
        # Blocking-read pump thread: asyncio add_reader on PTY masters is unreliable
        # with kqueue on macOS.
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                data = os.read(self.master_fd, 65536)
            except OSError:
                data = b""
            self.loop.call_soon_threadsafe(self._deliver, bytes(data))
            if not data:
                break

    def _deliver(self, data: bytes) -> None:
        if data:
            self.buffer.extend(data)
            if len(self.buffer) > BUFFER_LIMIT:
                del self.buffer[: len(self.buffer) - BUFFER_LIMIT]
        else:
            self.alive = False  # EOF: process exited; b"" propagates as the exit signal
        if self.subscriber is not None:
            self.subscriber.put_nowait(data)

    def attach(self, queue: asyncio.Queue[bytes]) -> bytes:
        """Make `queue` the live subscriber; returns buffered scrollback to replay."""
        self.subscriber = queue
        return bytes(self.buffer)

    def detach(self, queue: asyncio.Queue[bytes]) -> None:
        if self.subscriber is queue:
            self.subscriber = None

    def write(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def resize(self, cols: int, rows: int) -> None:
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def nudge_repaint(self) -> None:
        """SIGWINCH makes the TUI repaint after a scrollback replay."""
        try:
            os.killpg(self.proc.pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass

    def terminate(self) -> None:
        self.alive = False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.proc.poll() is not None:
                break
            try:
                os.killpg(self.proc.pid, sig)
                self.proc.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                continue
        try:
            os.close(self.master_fd)
        except OSError:
            pass


_session: TerminalSession | None = None


def get_or_create() -> TerminalSession:
    global _session
    if _session is None or not _session.alive:
        ws = ensure_workspace()
        if SESSION_ARGV is not None:
            argv, env = SESSION_ARGV, build_env(Path(SESSION_ARGV[0]))
        else:
            claude = find_claude_binary()
            argv, env = [str(claude)], build_env(claude)
        _session = TerminalSession(argv, cwd=ws, env=env)
    return _session


def restart() -> None:
    global _session
    if _session is not None:
        _session.terminate()
    _session = None
