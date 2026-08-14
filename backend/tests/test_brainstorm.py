import json
import sys
import textwrap

import pytest

from app.config import settings
from app.services import brainstorm as brainstorm_service

STREAM_STUB = textwrap.dedent(
    """\
    import json, sys
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"}))
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__copilot__search_memory", "input": {}},
    ]}}))
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Lead with the robotics story."},
    ]}}))
    print("not-json noise line")
    print(json.dumps({"type": "result", "session_id": "sess-1", "is_error": False}))
    """
)


def _sse_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


@pytest.fixture
def stub_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def install(source: str) -> None:
        stub = tmp_path / "stub.py"
        stub.write_text(source)
        monkeypatch.setattr(brainstorm_service, "BRAINSTORM_ARGV", [sys.executable, str(stub)])

    return install


def test_stream_happy_path(client, stub_claude):
    stub_claude(STREAM_STUB)
    with client.stream(
        "POST", "/api/brainstorm/message", json={"message": "what should I lead with?"}
    ) as resp:
        assert resp.status_code == 200
        events = _sse_events(resp)
    assert [e["type"] for e in events] == ["session", "tool", "text", "done"]
    assert events[0]["session_id"] == "sess-1"
    assert events[1]["name"] == "mcp__copilot__search_memory"
    assert "robotics" in events[2]["text"]
    assert events[3]["is_error"] is False


def test_stream_surfaces_process_failure(client, stub_claude):
    stub_claude("import sys; sys.stderr.write('claude exploded'); sys.exit(3)\n")
    with client.stream("POST", "/api/brainstorm/message", json={"message": "hi"}) as resp:
        events = _sse_events(resp)
    assert events[-1]["type"] == "error"
    assert "claude exploded" in events[-1]["message"]


def test_stream_times_out(client, stub_claude, monkeypatch):
    monkeypatch.setattr(brainstorm_service, "SESSION_TIMEOUT_SECONDS", 1)
    stub_claude("import time; time.sleep(10)\n")
    with client.stream("POST", "/api/brainstorm/message", json={"message": "hi"}) as resp:
        events = _sse_events(resp)
    assert events[-1]["type"] == "error"
    assert "timed out" in events[-1]["message"]


def test_resume_threads_session_id(tmp_path, monkeypatch):
    """Real argv construction: --resume only when a session id is provided,
    read-only tool allowlist, subscription workspace config."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    fake_claude = tmp_path / "claude"
    fake_claude.touch()
    monkeypatch.setattr(
        brainstorm_service.terminal_service, "find_claude_binary", lambda: fake_claude
    )
    argv = brainstorm_service.build_argv("hello", None)
    assert "--resume" not in argv
    assert "--strict-mcp-config" in argv
    tools = argv[argv.index("--allowedTools") + 1]
    assert "add_entry" not in tools  # writes go through the UI, not the session

    argv = brainstorm_service.build_argv("hello", "sess-9")
    assert argv[argv.index("--resume") + 1] == "sess-9"
