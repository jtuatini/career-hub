"""Enrichment: signals + drafts, scrubbed, truncated, failure-safe."""

from app.db.models import Person
from app.services import network


# The dash-heavy prefix below shrinks a lot under writing.scrub() (each
# "AB — " unit, 5 raw chars, becomes "AB, ", 4 chars). That shrinkage is what
# pins the required order: scrub-THEN-truncate lets the 300-char window
# reach past the whole (now-shrunk) prefix into the "TAILOK" marker, while
# truncate-THEN-scrub would cut the raw text off at index 300 — still
# entirely inside the un-scrubbed prefix — and never see the marker at all.
_DASH_PREFIX = "AB — " * 70  # 350 raw chars, all-dash-clause-separated
_TAIL_MARKER = "TAILOK"

PAYLOAD = {
    "match_signals": [{"signal": "school", "detail": "UMich AeroE alum"}],
    "summary": "Sarah leads GNC tooling at Acme.",
    "connection_note": _DASH_PREFIX + _TAIL_MARKER + " " + "padding " * 20,
    "followup": "Longer message — with an em-dash tell.",
}


def _person(db, **overrides):
    defaults = dict(name="Sarah Chen", company="Acme", headline="GNC Engineer", source="manual")
    defaults.update(overrides)
    p = Person(**defaults)
    db.add(p)
    db.commit()
    return p


def test_enrich_fills_scrubs_and_truncates(db_session, monkeypatch):
    captured = {}

    def fake_json(system, user_content, schema, max_tokens=16000):
        captured["system"] = system
        captured["user"] = user_content
        return PAYLOAD

    monkeypatch.setattr(network.engine, "generate_json", fake_json)
    monkeypatch.setattr(network.voice_service, "voice_context", lambda db: "VOICEBLOCK")
    p = network.enrich(db_session, _person(db_session))

    assert p.match_signals[0]["signal"] == "school"
    assert len(p.connection_note) <= 300
    assert "—" not in p.connection_note
    # Pins scrub-BEFORE-truncate specifically: this marker only survives into
    # the final 300 chars if scrub() ran on the full text first. See the
    # module docstring above _DASH_PREFIX for why.
    assert _TAIL_MARKER in p.connection_note
    assert "—" not in p.followup  # scrubbed
    assert "VOICEBLOCK" in captured["system"]
    assert "Writing rules" in captured["system"]  # AI_WRITING_RULES present
    assert "Sarah Chen" in captured["user"] and "Acme" in captured["user"]


def test_enrich_survives_engine_failure(db_session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(network.engine, "generate_json", boom)
    p = _person(
        db_session,
        summary="Pre-existing summary.",
        connection_note="Pre-existing note.",
        followup="Pre-existing followup.",
        match_signals=[{"signal": "school", "detail": "already enriched"}],
    )
    out = network.enrich(db_session, p)
    assert out.summary == "Pre-existing summary."
    assert out.connection_note == "Pre-existing note."
    assert out.followup == "Pre-existing followup."
    assert out.match_signals == [{"signal": "school", "detail": "already enriched"}]


def test_enrich_survives_partial_payload(db_session, monkeypatch):
    """A structurally-valid-but-incomplete engine reply (parses as JSON, but
    missing a required key) must not silently clobber previously-good
    fields. The CLI engine path doesn't schema-validate, so this has to be
    enforced in enrich() itself."""

    def partial_json(system, user_content, schema, max_tokens=16000):
        return {
            "match_signals": [],
            "connection_note": "New note.",
            "followup": "New followup.",
            # "summary" missing
        }

    monkeypatch.setattr(network.engine, "generate_json", partial_json)
    p = _person(
        db_session,
        summary="Pre-existing summary.",
        connection_note="Pre-existing note.",
        followup="Pre-existing followup.",
        match_signals=[{"signal": "school", "detail": "already enriched"}],
    )
    out = network.enrich(db_session, p)
    assert out.summary == "Pre-existing summary."
    assert out.connection_note == "Pre-existing note."
    assert out.followup == "Pre-existing followup."
    assert out.match_signals == [{"signal": "school", "detail": "already enriched"}]
