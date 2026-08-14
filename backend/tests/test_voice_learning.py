"""Edit-learning: diff the AI draft against the user's final, extract rules."""

from app.db.models import StyleProfile
from app.services import voice

DRAFT = "I am writing to express my sincere interest in the software engineering internship at Acme Corporation this coming summer season."
FINAL = "I want to build things at Acme this summer — here's why I'd be useful."


def _mk_profile(db, rules=None):
    p = StyleProfile(content="## Tone\nDirect.", learned_rules=rules or [])
    db.add(p)
    db.commit()
    return p


def test_learn_skips_without_profile(db_session):
    assert voice.learn_from_edit(db_session, DRAFT, FINAL, "cover letter") == {"skipped": True}


def test_learn_skips_trivial_edit(db_session, monkeypatch):
    _mk_profile(db_session)
    called = []
    monkeypatch.setattr(voice, "generate_json", lambda *a, **k: called.append(1) or {"rules": []})
    result = voice.learn_from_edit(db_session, DRAFT, DRAFT, "cover letter")
    assert result == {"skipped": True} and not called


def test_learn_appends_dated_rules(db_session, monkeypatch):
    profile = _mk_profile(db_session)
    monkeypatch.setattr(
        voice, "generate_json",
        lambda *a, **k: {"rules": ["Open with intent, not formality", "Cut 'sincere interest'"]},
    )
    result = voice.learn_from_edit(db_session, DRAFT, FINAL, "cover letter")
    assert result["added"] == 2
    db_session.refresh(profile)
    assert len(profile.learned_rules) == 2
    assert all(r["date"] and r["rule"] for r in profile.learned_rules)


def test_learn_caps_rules_at_30_dropping_oldest(db_session, monkeypatch):
    old = [{"date": "2026-01-01", "rule": f"old rule {i}"} for i in range(30)]
    profile = _mk_profile(db_session, rules=old)
    monkeypatch.setattr(voice, "generate_json", lambda *a, **k: {"rules": ["newest rule"]})
    voice.learn_from_edit(db_session, DRAFT, FINAL, "cover letter")
    db_session.refresh(profile)
    assert len(profile.learned_rules) == 30
    assert profile.learned_rules[-1]["rule"] == "newest rule"
    assert profile.learned_rules[0]["rule"] == "old rule 1"  # oldest dropped


def test_learn_contains_engine_failure(db_session):
    _mk_profile(db_session)
    # conftest autouse stub raises ClaudeError
    result = voice.learn_from_edit(db_session, DRAFT, FINAL, "cover letter")
    assert "error" in result
