"""engine.json model overrides: round-trip, key preservation, corruption tolerance."""

import pytest

from app.config import settings
from app.services import engine_prefs


@pytest.fixture
def prefs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def test_get_model_defaults_empty(prefs_dir):
    assert engine_prefs.get_model("claude") == ""


def test_set_and_get_model_round_trip(prefs_dir):
    engine_prefs.set_model("claude", "opus")
    assert engine_prefs.get_model("claude") == "opus"


def test_set_model_strips_whitespace(prefs_dir):
    engine_prefs.set_model("claude", "  sonnet  ")
    assert engine_prefs.get_model("claude") == "sonnet"


def test_set_model_preserves_provider_selection(prefs_dir):
    engine_prefs.set_provider("codex")
    engine_prefs.set_model("claude", "sonnet")
    assert engine_prefs.get_provider() == "codex"


def test_set_provider_preserves_models(prefs_dir):
    engine_prefs.set_model("gemini", "gemini-2.5-pro")
    engine_prefs.set_provider("gemini")
    assert engine_prefs.get_model("gemini") == "gemini-2.5-pro"


def test_unknown_provider_raises(prefs_dir):
    with pytest.raises(ValueError):
        engine_prefs.set_model("gpt6", "x")
    with pytest.raises(ValueError):
        engine_prefs.get_model("gpt6")


def test_get_model_tolerates_corrupt_values(prefs_dir):
    (settings.data_dir / "engine.json").write_text('{"models": {"claude": 42}}')
    assert engine_prefs.get_model("claude") == ""
    (settings.data_dir / "engine.json").write_text("not json")
    assert engine_prefs.get_model("claude") == ""
