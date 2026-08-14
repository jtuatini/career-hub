"""Voice training: samples, style profile, and edit-learning."""

from app.db.models import StyleProfile, VoiceSample
import pytest

from app.services import voice


def test_voice_models_roundtrip(db_session):
    sample = VoiceSample(title="Essay", kind="formal", source="essay.pdf", text="I wrote this.")
    profile = StyleProfile(content="## Tone\nDirect.", learned_rules=[{"date": "2026-07-28", "rule": "Cut adjectives"}])
    db_session.add_all([sample, profile])
    db_session.commit()
    assert db_session.get(VoiceSample, sample.id).kind == "formal"
    assert db_session.get(StyleProfile, profile.id).learned_rules[0]["rule"] == "Cut adjectives"


def _mk_sample(db, kind="formal", title="Essay", text="Thoughtful essay text here."):
    from app.db.models import VoiceSample

    s = VoiceSample(title=title, kind=kind, source="paste", text=text)
    db.add(s)
    db.commit()
    return s


def test_build_profile_requires_samples(db_session):
    with pytest.raises(ValueError, match="sample"):
        voice.build_profile(db_session)


def test_build_profile_creates_and_rebuild_preserves_rules(db_session, monkeypatch):
    _mk_sample(db_session, "formal")
    _mk_sample(db_session, "informal", title="Texts", text="lol ok here's the thing")
    seen: list[str] = []

    def fake(system, user_content, schema, max_tokens=16000):
        seen.append(user_content)
        return {"profile": "## Tone\nDirect, dry humor."}

    monkeypatch.setattr(voice, "generate_json", fake)
    profile = voice.build_profile(db_session)
    assert "Direct" in profile.content
    assert "[formal]" in seen[0] and "[informal]" in seen[0]

    profile.learned_rules = [{"date": "2026-07-28", "rule": "Shorter openings"}]
    db_session.commit()
    rebuilt = voice.build_profile(db_session)
    assert rebuilt.id == profile.id
    assert rebuilt.learned_rules[0]["rule"] == "Shorter openings"  # preserved


def test_voice_context_empty_without_profile(db_session):
    assert voice.voice_context(db_session) == ""


def test_voice_context_renders_profile_and_rules(db_session):
    from app.db.models import StyleProfile

    db_session.add(StyleProfile(content="## Tone\nDirect.", learned_rules=[{"date": "2026-07-28", "rule": "Cut filler"}]))
    db_session.commit()
    ctx = voice.voice_context(db_session)
    assert "Direct." in ctx and "Cut filler" in ctx


def test_critique_refine_returns_original_on_failure_or_no_profile(db_session):
    # no profile -> unchanged; voice_context returns "", so generate_json never called
    assert voice.critique_refine(db_session, "Dear team,", "Acme SWE") == "Dear team,"


def test_critique_refine_revises_with_profile(db_session, monkeypatch):
    from app.db.models import StyleProfile

    db_session.add(StyleProfile(content="## Tone\nDirect."))
    db_session.commit()
    monkeypatch.setattr(voice, "generate_json", lambda *a, **k: {"revised": "Hi Acme team,"})
    assert voice.critique_refine(db_session, "Dear team,", "Acme SWE") == "Hi Acme team,"


def test_critique_refine_survives_engine_failure_with_profile(db_session):
    # Profile exists, but generate_json is stubbed to raise by conftest autouse fixture.
    # Best-effort contract: critique_refine must return original body unchanged.
    from app.db.models import StyleProfile

    db_session.add(StyleProfile(content="## Tone\nDirect."))
    db_session.commit()
    # Do NOT monkeypatch generate_json; conftest autouse stub raises ClaudeError
    result = voice.critique_refine(db_session, "Dear team,", "Acme SWE")
    assert result == "Dear team,"
