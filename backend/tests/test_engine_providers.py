"""CLI provider units: argv construction, metered-key stripping, output parsing.
No real CLI is ever spawned — subprocess.run is faked per test."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers import claude_cli, codex_cli, gemini_cli


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _fake_run(captured, *, returncode=0, stdout="", stderr="", write_last_message=None):
    def run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        if write_last_message is not None:
            out = Path(argv[argv.index("--output-last-message") + 1])
            out.write_text(write_last_message)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


def test_codex_strips_openai_key_and_reads_last_message(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(codex_cli.shutil, "which", lambda n: "/opt/bin/codex")
    monkeypatch.setattr(codex_cli.subprocess, "run", _fake_run(captured, write_last_message="hello world"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-metered")
    assert codex_cli.generate_text("SYS", "USER") == "hello world"
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["argv"][0] == "/opt/bin/codex" and captured["argv"][1] == "exec"
    assert "SYS\n\nUSER" in captured["argv"]


def test_codex_failure_raises(sandbox, monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda n: "/opt/bin/codex")
    monkeypatch.setattr(
        codex_cli.subprocess, "run",
        _fake_run({}, returncode=2, stderr="login required", write_last_message=""),
    )
    with pytest.raises(ClaudeError, match="login required"):
        codex_cli.generate_text("SYS", "USER")


def test_codex_unavailable(monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda n: None)
    assert codex_cli.available() is False


def test_gemini_strips_google_keys_and_returns_stdout(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda n: "/opt/bin/gemini")
    monkeypatch.setattr(
        gemini_cli.subprocess, "run", _fake_run(captured, stdout="gemini says hi\n")
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g-metered")
    monkeypatch.setenv("GOOGLE_API_KEY", "g2-metered")
    assert gemini_cli.generate_text("SYS", "USER") == "gemini says hi"
    assert "GEMINI_API_KEY" not in captured["env"]
    assert "GOOGLE_API_KEY" not in captured["env"]
    assert captured["argv"][:2] == ["/opt/bin/gemini", "-p"]


def test_gemini_generate_json_retries_once_then_raises(sandbox, monkeypatch):
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda n: "/opt/bin/gemini")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="not json at all", stderr="")

    monkeypatch.setattr(gemini_cli.subprocess, "run", run)
    with pytest.raises((ValueError, ClaudeError)):
        gemini_cli.generate_json("SYS", "USER", {"type": "object"})
    assert len(calls) == 2  # one retry, then give up


def test_codex_model_override_beats_env_default(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(codex_cli.shutil, "which", lambda n: "/opt/bin/codex")
    monkeypatch.setattr(codex_cli.subprocess, "run", _fake_run(captured, write_last_message="ok"))
    monkeypatch.setattr(settings, "codex_model", "env-model")
    engine_prefs.set_model("codex", "gpt-5.3-codex")
    codex_cli.generate_text("SYS", "USER")
    i = captured["argv"].index("-m")
    assert captured["argv"][i + 1] == "gpt-5.3-codex"


def test_codex_empty_override_falls_back_to_env(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(codex_cli.shutil, "which", lambda n: "/opt/bin/codex")
    monkeypatch.setattr(codex_cli.subprocess, "run", _fake_run(captured, write_last_message="ok"))
    monkeypatch.setattr(settings, "codex_model", "env-model")
    codex_cli.generate_text("SYS", "USER")
    i = captured["argv"].index("-m")
    assert captured["argv"][i + 1] == "env-model"


def test_gemini_model_override_beats_env_default(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(gemini_cli.shutil, "which", lambda n: "/opt/bin/gemini")
    monkeypatch.setattr(
        gemini_cli.subprocess,
        "run",
        lambda argv, **k: (captured.__setitem__("argv", argv), SimpleNamespace(returncode=0, stdout="ok", stderr=""))[1],
    )
    monkeypatch.setattr(settings, "gemini_model", "env-model")
    engine_prefs.set_model("gemini", "gemini-2.5-pro")
    gemini_cli.generate_text("SYS", "USER")
    i = captured["argv"].index("-m")
    assert captured["argv"][i + 1] == "gemini-2.5-pro"


def test_claude_model_override_beats_env_default(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(claude_cli.terminal_service, "find_claude_binary", lambda: Path("/opt/bin/claude"))
    monkeypatch.setattr(claude_cli.terminal_service, "ensure_workspace", lambda: Path("/tmp"))
    monkeypatch.setattr(claude_cli.terminal_service, "build_env", lambda b: {})

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout='{"result": "ok"}', stderr="")

    monkeypatch.setattr(claude_cli.subprocess, "run", run)
    engine_prefs.set_model("claude", "sonnet")
    claude_cli.generate_text("SYS", "USER")
    i = captured["argv"].index("--model")
    assert captured["argv"][i + 1] == "sonnet"


def test_claude_no_override_uses_env_default(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(claude_cli.terminal_service, "find_claude_binary", lambda: Path("/opt/bin/claude"))
    monkeypatch.setattr(claude_cli.terminal_service, "ensure_workspace", lambda: Path("/tmp"))
    monkeypatch.setattr(claude_cli.terminal_service, "build_env", lambda b: {})

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout='{"result": "ok"}', stderr="")

    monkeypatch.setattr(claude_cli.subprocess, "run", run)
    claude_cli.generate_text("SYS", "USER")
    i = captured["argv"].index("--model")
    assert captured["argv"][i + 1] == settings.claude_model
