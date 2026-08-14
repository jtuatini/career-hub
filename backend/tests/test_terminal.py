import json
from pathlib import Path

import pytest

from app.config import settings
from app.services import terminal as terminal_service

MINIMAL_TEX = "\\documentclass{article}\\begin{document}hello\\end{document}"


@pytest.fixture
def workspace_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(terminal_service, "SESSION_ARGV", ["/bin/cat"])
    yield tmp_path
    terminal_service.restart()


def test_workspace_provisioning(workspace_env):
    ws = terminal_service.ensure_workspace()
    assert ws == workspace_env / "ai-workspace"
    assert "wording only" in (ws / "CLAUDE.md").read_text()

    mcp_config = json.loads((ws / ".mcp.json").read_text())
    server = mcp_config["mcpServers"]["copilot"]
    assert Path(server["command"]).is_absolute()
    assert server["args"][:2] == ["run", "--directory"]
    assert Path(server["args"][2]) == terminal_service.BACKEND_DIR

    claude_settings = json.loads((ws / ".claude" / "settings.json").read_text())
    assert claude_settings["enableAllProjectMcpServers"] is True


def test_build_env_strips_api_key_and_sets_term(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    env = terminal_service.build_env(Path("/opt/bin/claude"))
    assert "ANTHROPIC_API_KEY" not in env
    assert env["TERM"] == "xterm-256color"
    assert env["PATH"].startswith("/opt/bin:")


def test_find_claude_binary_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(FileNotFoundError):
        terminal_service.find_claude_binary()

    fallback = tmp_path / ".local" / "bin" / "claude"
    fallback.parent.mkdir(parents=True)
    fallback.touch()
    assert terminal_service.find_claude_binary() == fallback


def test_websocket_echo_and_replay(client, workspace_env):
    with client.websocket_connect("/api/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_bytes(b"hello\n")
        received = b""
        while b"hello" not in received:
            received += ws.receive_bytes()

    # Session survived the disconnect; scrollback is replayed on reattach.
    with client.websocket_connect("/api/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
        assert b"hello" in ws.receive_bytes()


def test_restart_endpoint_discards_session(client, workspace_env):
    with client.websocket_connect("/api/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_bytes(b"before-restart\n")
        received = b""
        while b"before-restart" not in received:
            received += ws.receive_bytes()

    old_proc = terminal_service._session.proc
    assert client.post("/api/terminal/restart").status_code == 200
    assert terminal_service._session is None
    assert old_proc.poll() is not None  # stub process was terminated

    with client.websocket_connect("/api/terminal/ws", headers={"origin": "http://localhost:5173"}) as ws:
        ws.send_bytes(b"fresh\n")
        received = b""
        while b"fresh" not in received:
            received += ws.receive_bytes()
        assert b"before-restart" not in received


@pytest.mark.parametrize("headers", [
    {"origin": "https://evil.example"},
    {},  # absent Origin must be rejected too — the gate never fails open
])
def test_websocket_rejects_untrusted_origin(client, workspace_env, headers):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/terminal/ws", headers=headers):
            pass
    assert exc.value.code == 1008
