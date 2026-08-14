import json
import os
import stat as stat_mod

import pytest

from app.config import settings
from app.services import claude as api_engine
from app.services import engine
from app.services import terminal as terminal_service
from app.services.claude import ClaudeError

STUB = """#!/usr/bin/env python3
import json, os, sys

if os.environ.get("STUB_ENV_DUMP"):
    with open(os.environ["STUB_ENV_DUMP"], "w") as f:
        json.dump(dict(os.environ), f)

mode = os.environ.get("STUB_MODE", "ok")
if mode == "fail":
    sys.stderr.write("stub exploded")
    sys.exit(3)

counter_path = os.environ.get("STUB_COUNTER")
calls = 0
if counter_path:
    calls = int(open(counter_path).read()) if os.path.exists(counter_path) else 0
    open(counter_path, "w").write(str(calls + 1))

if mode == "bad-then-good" and calls == 0:
    result = "sorry, here you go: not json at all"
elif mode == "fenced":
    result = "```json\\n{\\"company\\": \\"Acme\\"}\\n```"
elif mode == "text":
    result = "plain prose answer"
else:
    result = json.dumps({"company": "Acme"})

print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result}))
"""

SCHEMA = {"type": "object", "properties": {"company": {"type": "string"}}, "required": ["company"]}


@pytest.fixture
def stub_engine(tmp_path, monkeypatch):
    stub = tmp_path / "claude-stub"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat_mod.S_IEXEC)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(terminal_service, "find_claude_binary", lambda: stub)
    monkeypatch.setattr(engine, "last_used", None)
    return tmp_path


def test_auto_prefers_subscription_and_strips_api_key(stub_engine, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    dump = stub_engine / "env.json"
    monkeypatch.setenv("STUB_ENV_DUMP", str(dump))
    result = engine.generate_json("sys", "user", SCHEMA)
    assert result == {"company": "Acme"}
    assert engine.last_used == "subscription"
    child_env = json.loads(dump.read_text())
    assert "ANTHROPIC_API_KEY" not in child_env


def test_fenced_json_is_unwrapped(stub_engine, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fenced")
    assert engine.generate_json("sys", "user", SCHEMA) == {"company": "Acme"}


def test_bad_json_retries_once(stub_engine, monkeypatch):
    counter = stub_engine / "counter"
    monkeypatch.setenv("STUB_MODE", "bad-then-good")
    monkeypatch.setenv("STUB_COUNTER", str(counter))
    assert engine.generate_json("sys", "user", SCHEMA) == {"company": "Acme"}
    assert counter.read_text() == "2"


def test_cli_failure_falls_back_to_api(stub_engine, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-configured")
    monkeypatch.setattr(
        api_engine, "generate_json", lambda *a, **kw: {"company": "FromAPI"}
    )
    assert engine.generate_json("sys", "user", SCHEMA) == {"company": "FromAPI"}
    assert engine.last_used == "api"


def test_subscription_only_failure_raises(stub_engine, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "fail")
    monkeypatch.setattr(settings, "ai_engine", "subscription")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-configured")
    with pytest.raises(ClaudeError, match="stub exploded|no API fallback"):
        engine.generate_json("sys", "user", SCHEMA)


def test_generate_text_via_subscription(stub_engine, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "text")
    assert engine.generate_text("sys", "user") == "plain prose answer"


def test_no_cli_no_key_is_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(
        terminal_service, "find_claude_binary", lambda: (_ for _ in ()).throw(FileNotFoundError())
    )
    with pytest.raises(ClaudeError):
        engine.generate_json("sys", "user", SCHEMA)


def test_status_endpoint(client):
    # Shape deliberately extended by the provider-registry work (Task 13):
    # the legacy 4 keys keep their existing value semantics, plus
    # ai_provider/providers/last_provider for the Task 14 provider picker.
    # Subset/valued assertions (not exact-set equality) so future additive
    # keys don't re-trip this test.
    body = client.get("/api/engine/status").json()
    assert body["engine_preference"] == settings.ai_engine
    assert body["subscription_available"] in (True, False)
    assert body["api_key_configured"] in (True, False)
    assert "last_used" in body and body["last_used"] in (None, "subscription", "api")
    assert body["ai_provider"] == "claude"
    assert set(body["providers"]) == {"claude", "codex", "gemini"}
    assert all(isinstance(v, bool) for v in body["providers"].values())
    assert "last_provider" in body


def test_image_run_permits_read_tool(stub_engine, monkeypatch):
    """generate_json_with_image needs the Read tool to see the screenshot, and
    deny rules beat allow rules in the CLI — so Read must drop out of
    --disallowedTools when it is explicitly allowed."""
    argv_seen = {}

    def fake_exec(argv, ws, claude_bin, timeout):
        argv_seen["argv"] = argv
        return json.dumps({"company": "Acme"})

    monkeypatch.setattr(engine, "_exec_cli", fake_exec)
    assert engine.generate_json_with_image("sys", b"png-bytes", SCHEMA) == {"company": "Acme"}

    argv = argv_seen["argv"]
    allowed = argv[argv.index("--allowedTools") + 1]
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert "Read" in allowed
    assert "Read" not in disallowed
    assert "Bash" in disallowed and "Write" in disallowed


def test_plain_generation_still_disallows_read(stub_engine, monkeypatch):
    argv_seen = {}

    def fake_exec(argv, ws, claude_bin, timeout):
        argv_seen["argv"] = argv
        return "prose"

    monkeypatch.setattr(engine, "_exec_cli", fake_exec)
    engine.generate_text("sys", "user")
    disallowed = argv_seen["argv"][argv_seen["argv"].index("--disallowedTools") + 1]
    assert "Read" in disallowed


# ---------------------------------------------------- web-grounded search


def test_extract_urls_dedupes_and_strips_trailing_punctuation():
    text = "See https://acme.com/team, and (https://news.example/acme). Again https://acme.com/team"
    assert engine._extract_urls(text) == ["https://acme.com/team", "https://news.example/acme"]


def test_generate_search_prefers_subscription_and_allows_websearch(stub_engine, monkeypatch):
    """The CLI's own WebSearch replaces the API's server-side web_search, so a
    broken/absent API key can no longer block research or people discovery."""
    argv_seen = {}

    def fake_exec(argv, ws, claude_bin, timeout):
        argv_seen["argv"] = argv
        argv_seen["timeout"] = timeout
        return "Findings here. Source: https://acme.example/about"

    monkeypatch.setattr(engine, "_exec_cli", fake_exec)

    def boom(*a, **k):
        raise AssertionError("API must not be called when the subscription path works")

    monkeypatch.setattr(engine, "_api_search", boom)

    text, sources = engine.generate_search("SYS", "Research Acme.")
    assert "Findings here." in text
    assert sources == ["https://acme.example/about"]
    assert engine.last_used == "subscription"

    argv = argv_seen["argv"]
    allowed = argv[argv.index("--allowedTools") + 1]
    disallowed = argv[argv.index("--disallowedTools") + 1]
    assert "WebSearch" in allowed
    assert "WebSearch" not in disallowed  # the generation path disallows it; search must not
    assert "Bash" in disallowed and "Write" in disallowed  # still no shell/filesystem
    assert argv_seen["timeout"] == engine.SEARCH_TIMEOUT_SECONDS


def test_generate_search_falls_back_to_api_when_cli_fails(stub_engine, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    def cli_boom(*a, **k):
        raise ClaudeError("cli down")

    monkeypatch.setattr(engine, "_run_cli_search", cli_boom)
    monkeypatch.setattr(engine, "_api_search", lambda s, u, m: ("api text", ["https://x.example"]))

    text, sources = engine.generate_search("SYS", "Research Acme.")
    assert text == "api text" and sources == ["https://x.example"]
    assert engine.last_used == "api"
