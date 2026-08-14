"""CLI provider units: argv construction, metered-key stripping, output parsing.
No real CLI is ever spawned — subprocess.run is faked per test."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers import antigravity_cli, claude_cli, codex_cli


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


def test_antigravity_strips_google_keys_and_parses_envelope(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")
    monkeypatch.setattr(
        antigravity_cli.subprocess,
        "run",
        _fake_run(captured, stdout='{"status": "SUCCESS", "response": "agy says hi\\n"}'),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "g-metered")
    monkeypatch.setenv("GOOGLE_API_KEY", "g2-metered")
    assert antigravity_cli.generate_text("SYS", "USER") == "agy says hi"
    assert "GEMINI_API_KEY" not in captured["env"]
    assert "GOOGLE_API_KEY" not in captured["env"]
    assert captured["argv"][:2] == ["/opt/bin/agy", "-p"]
    # Untrusted prompt content must stay contained in headless runs.
    assert "--sandbox" in captured["argv"]
    assert "--disable-slash-commands" in captured["argv"]


def test_antigravity_failed_status_raises(sandbox, monkeypatch):
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")
    monkeypatch.setattr(
        antigravity_cli.subprocess,
        "run",
        _fake_run({}, stdout='{"status": "ERROR", "response": ""}'),
    )
    with pytest.raises(ClaudeError):
        antigravity_cli.generate_text("SYS", "USER")


def test_antigravity_generate_json_prefers_structured_output(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")
    monkeypatch.setattr(
        antigravity_cli.subprocess,
        "run",
        _fake_run(
            captured,
            stdout='{"status": "SUCCESS", "response": "{\\"a\\": 1}", "structured_output": {"a": 1}}',
        ),
    )
    assert antigravity_cli.generate_json("SYS", "USER", {"type": "object"}) == {"a": 1}
    assert "--json-schema" in captured["argv"]


def test_antigravity_generate_json_falls_back_to_response_text(sandbox, monkeypatch):
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")
    monkeypatch.setattr(
        antigravity_cli.subprocess,
        "run",
        _fake_run({}, stdout='{"status": "SUCCESS", "response": "```json\\n{\\"b\\": 2}\\n```"}'),
    )
    assert antigravity_cli.generate_json("SYS", "USER", {"type": "object"}) == {"b": 2}


def test_antigravity_non_json_output_raises(sandbox, monkeypatch):
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")
    monkeypatch.setattr(
        antigravity_cli.subprocess, "run", _fake_run({}, stdout="not json at all")
    )
    with pytest.raises(ClaudeError):
        antigravity_cli.generate_json("SYS", "USER", {"type": "object"})


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


def test_antigravity_model_override_beats_env_default(sandbox, monkeypatch):
    captured = {}
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")
    monkeypatch.setattr(
        antigravity_cli.subprocess,
        "run",
        _fake_run(captured, stdout='{"status": "SUCCESS", "response": "ok"}'),
    )
    monkeypatch.setattr(settings, "antigravity_model", "env-model")
    engine_prefs.set_model("antigravity", "gemini-3.7-flash-high")
    antigravity_cli.generate_text("SYS", "USER")
    i = captured["argv"].index("--model")
    assert captured["argv"][i + 1] == "gemini-3.7-flash-high"


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


# ---------------------------------------------------------------- custom CLI


def _custom_setup(monkeypatch, command, model=""):
    from app.services.engine_providers import custom_cli

    engine_prefs.set_custom_command(command)
    if model:
        engine_prefs.set_model("custom", model)
    monkeypatch.setattr(custom_cli.shutil, "which", lambda n: f"/opt/bin/{n}")
    return custom_cli


def test_custom_bare_ollama_expands_and_pipes_stdin(sandbox, monkeypatch):
    custom_cli = _custom_setup(monkeypatch, "ollama", model="llama3.2")
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="local says hi\n", stderr="")

    monkeypatch.setattr(custom_cli.subprocess, "run", run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "metered")
    assert custom_cli.generate_text("SYS", "USER") == "local says hi"
    assert captured["argv"] == ["ollama", "run", "llama3.2"]
    assert captured["input"] == "SYS\n\nUSER"  # no {prompt} placeholder -> stdin
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_custom_prompt_placeholder_goes_in_argv(sandbox, monkeypatch):
    custom_cli = _custom_setup(monkeypatch, "mycli --ask {prompt}")
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(custom_cli.subprocess, "run", run)
    custom_cli.generate_text("SYS", "USER")
    assert captured["argv"] == ["mycli", "--ask", "SYS\n\nUSER"]
    assert captured["input"] is None


def test_custom_bare_model_placeholder_dropped_when_unset(sandbox, monkeypatch):
    custom_cli = _custom_setup(monkeypatch, "mycli {model}")
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(custom_cli.subprocess, "run", run)
    custom_cli.generate_text("SYS", "USER")
    assert captured["argv"] == ["mycli"]


def test_custom_unconfigured_is_unavailable_and_raises(sandbox, monkeypatch):
    from app.services.engine_providers import custom_cli

    assert custom_cli.available() is False
    with pytest.raises(ClaudeError):
        custom_cli.generate_text("SYS", "USER")


def test_custom_missing_binary_unavailable(sandbox, monkeypatch):
    from app.services.engine_providers import custom_cli

    engine_prefs.set_custom_command("nonexistent-cli")
    monkeypatch.setattr(custom_cli.shutil, "which", lambda n: None)
    assert custom_cli.available() is False


def test_custom_strips_ansi_codes_from_output(sandbox, monkeypatch):
    custom_cli = _custom_setup(monkeypatch, "mycli")
    monkeypatch.setattr(
        custom_cli.subprocess,
        "run",
        lambda argv, **k: SimpleNamespace(
            returncode=0, stdout="thinking\x1b[2D\x1b[K\rBlue\n", stderr=""
        ),
    )
    assert custom_cli.generate_text("SYS", "USER") == "thinkingBlue"


def test_antigravity_envelope_error_is_readable(sandbox, monkeypatch):
    """agy failures arrive as exit 1 + a JSON envelope; the user must see the
    envelope's error message, never the raw JSON blob."""
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda n: "/opt/bin/agy")

    def run(argv, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout='{"status": "ERROR", "response": "", "error": "Eligibility check failed: connection reset by peer"}',
            stderr="",
        )

    monkeypatch.setattr(antigravity_cli.subprocess, "run", run)
    with pytest.raises(ClaudeError) as exc:
        antigravity_cli.generate_text("SYS", "USER")
    message = str(exc.value)
    assert "couldn't reach Google" in message
    assert '"conversation_id"' not in message and '"usage"' not in message
