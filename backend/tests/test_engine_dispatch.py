"""Dispatch chain: default behavior byte-identical to today; chosen provider
first; capability fallthrough; ai_engine semantics preserved."""

import pytest

from app.config import settings
from app.services import engine, engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers import claude_cli, codex_cli, antigravity_cli


@pytest.fixture
def prefs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def test_default_chain_is_claude_then_api(prefs_dir, monkeypatch):
    """No engine.json → exactly today's behavior: claude CLI first, API fallback."""
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "generate_text", lambda s, u: "cli-out")
    assert engine.generate_text("s", "u") == "cli-out"
    assert engine.last_used == "subscription"


def test_default_chain_falls_back_to_api(prefs_dir, monkeypatch):
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-x")
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    def boom(s, u):
        raise ClaudeError("cli down")

    monkeypatch.setattr(claude_cli, "generate_text", boom)
    monkeypatch.setattr(engine.api_engine, "generate_text", lambda s, u, m=16000: "api-out")
    assert engine.generate_text("s", "u") == "api-out"
    assert engine.last_used == "api"


def test_selected_provider_runs_first(prefs_dir, monkeypatch):
    engine_prefs.set_provider("antigravity")
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(antigravity_cli, "available", lambda: True)
    monkeypatch.setattr(antigravity_cli, "generate_text", lambda s, u: "antigravity-out")
    monkeypatch.setattr(claude_cli, "generate_text", lambda s, u: "claude-out")
    assert engine.generate_text("s", "u") == "antigravity-out"
    assert engine.last_provider == "antigravity"


def test_selected_provider_failure_falls_through_to_claude(prefs_dir, monkeypatch):
    engine_prefs.set_provider("codex")
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(codex_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "available", lambda: True)

    def boom(s, u):
        raise ClaudeError("codex down")

    monkeypatch.setattr(codex_cli, "generate_text", boom)
    monkeypatch.setattr(claude_cli, "generate_text", lambda s, u: "claude-out")
    assert engine.generate_text("s", "u") == "claude-out"
    assert engine.last_provider == "claude"


def test_capability_fallthrough_for_image(prefs_dir, monkeypatch):
    """codex has no image capability → image calls go straight to claude."""
    engine_prefs.set_provider("codex")
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(claude_cli, "image", lambda s, png, sch: {"ok": True})
    assert engine.generate_json_with_image("s", b"png", {"type": "object"}) == {"ok": True}


def test_api_mode_skips_all_clis(prefs_dir, monkeypatch):
    engine_prefs.set_provider("antigravity")
    monkeypatch.setattr(settings, "ai_engine", "api")
    monkeypatch.setattr(engine.api_engine, "generate_text", lambda s, u, m=16000: "api-out")
    assert engine.generate_text("s", "u") == "api-out"


def test_prefs_roundtrip_and_default(prefs_dir):
    assert engine_prefs.get_provider() == "claude"
    engine_prefs.set_provider("codex")
    assert engine_prefs.get_provider() == "codex"


def test_status_reports_providers(prefs_dir, monkeypatch):
    monkeypatch.setattr(claude_cli, "available", lambda: True)
    monkeypatch.setattr(codex_cli, "available", lambda: False)
    monkeypatch.setattr(antigravity_cli, "available", lambda: False)
    st = engine.status()
    assert st["ai_provider"] == "claude"
    assert st["providers"] == {
        "claude": True, "codex": False, "antigravity": False, "custom": False,
    }
    assert st["subscription_available"] is True  # legacy key preserved


# ---------------------------------------------------- Finding 1 regression


def test_auto_mode_all_unavailable_no_key_surfaces_actionable_api_error(prefs_dir, monkeypatch):
    """First-run scenario: auto mode, nothing installed, no API key configured.
    Nothing was actually attempted (every provider skipped by available()), so
    dispatch must fall through to the real API path — which raises claude.py's
    actionable "no key" error — rather than a generic "no CLI provider" one."""
    monkeypatch.setattr(settings, "ai_engine", "auto")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(claude_cli, "available", lambda: False)
    monkeypatch.setattr(codex_cli, "available", lambda: False)
    monkeypatch.setattr(antigravity_cli, "available", lambda: False)
    with pytest.raises(ClaudeError, match="ANTHROPIC_API_KEY is not set"):
        engine.generate_text("s", "u")


# ---------------------------------------------------- subscription all-fail


def test_subscription_mode_all_fail_aggregates_errors_no_api_fallback(prefs_dir, monkeypatch):
    """subscription mode never falls back to the API — every CLI in the chain
    is tried (no availability skip), and if all fail the aggregated error
    names every provider that was attempted."""
    engine_prefs.set_provider("codex")
    monkeypatch.setattr(settings, "ai_engine", "subscription")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-x")

    def codex_boom(s, u):
        raise ClaudeError("codex exploded")

    def claude_boom(s, u):
        raise ClaudeError("claude exploded")

    monkeypatch.setattr(codex_cli, "generate_text", codex_boom)
    monkeypatch.setattr(claude_cli, "generate_text", claude_boom)

    def api_boom(*a, **kw):
        raise AssertionError("API must not be called in subscription mode")

    monkeypatch.setattr(engine.api_engine, "generate_text", api_boom)

    with pytest.raises(ClaudeError) as exc_info:
        engine.generate_text("s", "u")
    message = str(exc_info.value)
    assert "codex" in message and "codex exploded" in message
    assert "claude" in message and "claude exploded" in message


# ---------------------------------------------------- engine_prefs validation


def test_set_provider_rejects_unknown_name(prefs_dir):
    with pytest.raises(ValueError):
        engine_prefs.set_provider("gpt5")


def test_get_provider_defaults_on_unknown_value_in_file(prefs_dir):
    (prefs_dir / "engine.json").write_text('{"ai_provider": "banana"}')
    assert engine_prefs.get_provider() == "claude"


def test_get_provider_defaults_on_corrupt_json(prefs_dir):
    (prefs_dir / "engine.json").write_text("{not valid json")
    assert engine_prefs.get_provider() == "claude"


# ---------------------------------------------------- PUT /api/engine/provider


def test_put_provider_endpoint_switches_and_returns_full_status(client):
    """client fixture (conftest.py) isolates settings.data_dir to a tmp_path,
    so this never touches the owner's real data/engine.json."""
    resp = client.put("/api/engine/provider", json={"provider": "antigravity"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_provider"] == "antigravity"
    assert set(body) == {
        "engine_preference",
        "ai_provider",
        "providers",
        "models",
        "model_defaults",
        "custom_command",
        "subscription_available",
        "api_key_configured",
        "last_used",
        "last_provider",
    }


def test_put_provider_endpoint_rejects_unknown_provider(client):
    resp = client.put("/api/engine/provider", json={"provider": "banana"})
    assert resp.status_code == 422
