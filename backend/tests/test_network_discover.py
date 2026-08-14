"""Discovery: parse, evidence-discard, dedupe/upsert, cooldown."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db.models import NetworkTarget, Person
from app.services import network

PEOPLE_JSON = """Here are the people I found:
```json
[
  {"name": "Sarah Chen", "headline": "GNC Engineer at Acme", "person_type": "engineer",
   "evidence_url": "https://acme.com/team/sarah", "reason": "does the exact target role"},
  {"name": "Ghost Person", "headline": "Recruiter", "person_type": "recruiter",
   "evidence_url": "", "reason": "no source"},
  {"name": "Raj Patel", "headline": "University Recruiting at Acme", "person_type": "recruiter",
   "evidence_url": "https://linkedin.com/in/rajp", "reason": "owns intern hiring"}
]
```"""


def _stub_search(monkeypatch, text, sources=(), calls=None):
    """Stub the engine's web-grounded search seam (subscription CLI first,
    metered API fallback) — discover() no longer talks to the API directly."""
    def fake(system, user_content, max_uses=8):
        if calls is not None:
            calls.append(user_content)
        return text, list(sources)

    monkeypatch.setattr(network.engine, "generate_search", fake)


@pytest.fixture
def target(db_session):
    t = NetworkTarget(company="Acme", role_type="GNC", source="manual")
    db_session.add(t)
    db_session.commit()
    return t


def test_parse_people_extracts_fenced_json():
    people = network._parse_people(PEOPLE_JSON)
    assert [p["name"] for p in people] == ["Sarah Chen", "Ghost Person", "Raj Patel"]


def test_discover_discards_missing_evidence_and_upserts(db_session, target, monkeypatch):
    _stub_search(monkeypatch, PEOPLE_JSON, sources=["https://acme.com/team"])

    found = network.discover(db_session, target)
    names = {p.name for p in found}
    assert names == {"Sarah Chen", "Raj Patel"}  # Ghost discarded: no evidence URL
    sarah = db_session.query(Person).filter_by(name="Sarah Chen").one()
    assert sarah.source == "web_search" and sarah.company == "Acme"
    assert "https://acme.com/team/sarah" in sarah.evidence_urls
    assert target.discovered_at is not None


def test_discover_upsert_preserves_user_edits(db_session, target, monkeypatch):
    db_session.add(Person(name="sarah chen", company="acme", source="linkedin_capture",
                          status="contacted", notes="met at fair"))
    db_session.commit()
    _stub_search(monkeypatch, PEOPLE_JSON)

    network.discover(db_session, target, force=True)
    assert db_session.query(Person).filter(Person.name.ilike("sarah chen")).count() == 1
    p = db_session.query(Person).filter(Person.name.ilike("sarah chen")).one()
    assert p.status == "contacted" and p.notes == "met at fair"  # untouched
    assert "https://acme.com/team/sarah" in p.evidence_urls  # evidence merged


def test_discover_profile_url_only_accepted_when_http(db_session, target, monkeypatch):
    """Finding 2: a discovered profile_url is rendered as a clickable href in
    NetworkView — a prompt-injected search result yielding a javascript: URL
    must never be stored. New-person branch."""
    payload = """```json
[
  {"name": "Sam Fox", "headline": "Engineer", "person_type": "engineer",
   "evidence_url": "https://acme.com/team/sam", "profile_url": "javascript:alert(1)",
   "reason": "malicious profile_url must be dropped"},
  {"name": "Mia Ortiz", "headline": "Recruiter", "person_type": "recruiter",
   "evidence_url": "https://acme.com/team/mia", "profile_url": "https://linkedin.com/in/miaortiz",
   "reason": "legit https profile_url is kept"}
]
```"""
    _stub_search(monkeypatch, payload)

    network.discover(db_session, target)
    sam = db_session.query(Person).filter_by(name="Sam Fox").one()
    mia = db_session.query(Person).filter_by(name="Mia Ortiz").one()
    assert sam.profile_url is None
    assert mia.profile_url == "https://linkedin.com/in/miaortiz"


def test_discover_backfill_profile_url_only_accepted_when_http(db_session, target, monkeypatch):
    """Finding 2, backfill-on-existing branch: an existing person with no
    profile_url must not get backfilled from a javascript: URL, but a legit
    https URL still backfills."""
    db_session.add(Person(name="Sam Fox", company="Acme", source="linkedin_capture", profile_url=None))
    db_session.commit()

    malicious = """```json
[
  {"name": "Sam Fox", "headline": "Engineer", "person_type": "engineer",
   "evidence_url": "https://acme.com/team/sam", "profile_url": "javascript:alert(1)",
   "reason": "malicious profile_url must not backfill"}
]
```"""
    _stub_search(monkeypatch, malicious)
    network.discover(db_session, target, force=True)
    sam = db_session.query(Person).filter_by(name="Sam Fox").one()
    assert sam.profile_url is None

    legit = malicious.replace("javascript:alert(1)", "https://linkedin.com/in/samfox")
    _stub_search(monkeypatch, legit)
    network.discover(db_session, target, force=True)
    sam = db_session.query(Person).filter_by(name="Sam Fox").one()
    assert sam.profile_url == "https://linkedin.com/in/samfox"


def test_discover_cooldown(db_session, target, monkeypatch):
    target.discovered_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()
    called = []
    _stub_search(monkeypatch, PEOPLE_JSON, calls=called)
    assert network.discover(db_session, target) == []
    assert called == []  # cooldown: no engine call


def test_discover_cooldown_naive_datetime_within_window(db_session, target, monkeypatch):
    """SQLite genuinely returns naive datetimes on a fresh read (unlike Postgres,
    it doesn't enforce tzinfo on round-trip). discover() must normalize a naive
    discovered_at to UTC before comparing, not crash or mis-compare. This test's
    conftest sessionmaker uses expire_on_commit=False, so assigning a naive
    datetime here and committing is the simplest honest way to reproduce that
    naive value inside discover() without a real cross-session re-fetch."""
    target.discovered_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    db_session.commit()
    called = []
    _stub_search(monkeypatch, PEOPLE_JSON, calls=called)
    assert network.discover(db_session, target) == []
    assert called == []  # still within cooldown: no engine call


def test_discover_cooldown_naive_datetime_expired(db_session, target, monkeypatch):
    """Complementary case: a naive discovered_at outside the 24h window must
    still trigger a fresh discovery call."""
    target.discovered_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=25)
    db_session.commit()
    _stub_search(monkeypatch, PEOPLE_JSON)
    found = network.discover(db_session, target)
    assert {p.name for p in found} == {"Sarah Chen", "Raj Patel"}
