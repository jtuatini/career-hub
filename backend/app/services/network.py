"""Networking dashboard: target sync, people discovery, outreach enrichment.

Discovery uses the Anthropic API's server-side web-search (research.py's
pattern) — only company + role-type strings leave the machine, and people
without a public evidence URL are discarded. Enrichment runs on the engine
facade (subscription-first) and never needs web access."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Job, NetworkTarget, Person
from app.services import engine
from app.services import autofill as autofill_service
from app.services import memory as memory_service
from app.services import voice as voice_service
from app.services import writing

logger = logging.getLogger(__name__)

APPLIED_FAMILY = ("applied", "interview", "offer")

DISCOVER_SYSTEM = """\
You find real, currently-employed people at a company that an internship
applicant should reach out to. Use web search. Prioritize, in
order: alumni of the applicant's school at the company (any role), engineers
working in the given role area, university/early-career recruiters, and
managers of matching teams.

Return ONLY a fenced ```json block: a list of objects with keys
name, headline, person_type (one of alumni|engineer|recruiter|manager|other),
evidence_url (a public page proving this person exists in this role — REQUIRED,
use "" only if you truly found none), profile_url (public LinkedIn URL if one
appeared in results, else ""), location, reason (one line).
Never invent people. Fewer, verified results beat many guesses."""

_JSON_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL)

COOLDOWN = timedelta(hours=24)


def sync_targets(db: Session) -> None:
    """One active source='application' target per distinct company among
    applied-family jobs; retracted companies get deactivated. Idempotent;
    never touches manual targets.

    Finding 5: a company that already has an ACTIVE manual target is skipped
    entirely — that manual target already covers discovery for the company,
    so a derived target alongside it would be a second chip doubling
    discovery spend. Any existing derived row for such a company is
    deactivated (not deleted, so it reactivates cleanly if the manual target
    is later retracted)."""
    applied = {
        c.lower(): c
        for (c,) in db.execute(
            select(Job.company).where(Job.status.in_(APPLIED_FAMILY)).distinct()
        )
    }
    manual_active_companies = {
        t.company.lower()
        for t in db.scalars(
            select(NetworkTarget).where(
                NetworkTarget.source == "manual", NetworkTarget.active.is_(True)
            )
        ).all()
    }
    derived = db.scalars(
        select(NetworkTarget).where(NetworkTarget.source == "application")
    ).all()
    seen = set()
    for t in derived:
        key = t.company.lower()
        seen.add(key)
        t.active = key in applied and key not in manual_active_companies
    for key, company in applied.items():
        if key not in seen and key not in manual_active_companies:
            db.add(NetworkTarget(company=company, source="application"))
    db.commit()


def _parse_people(text: str) -> list[dict]:
    m = _JSON_FENCE.search(text)
    raw = m.group(1) if m else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _upsert_person(db: Session, data: dict, company: str, source: str) -> Person | None:
    name = (data.get("name") or "").strip()
    evidence = (data.get("evidence_url") or "").strip()
    if not name or (source == "web_search" and not evidence.startswith("http")):
        return None
    # profile_url is rendered as a clickable href in NetworkView — a
    # prompt-injected search result (or a manual submission) yielding
    # javascript:... or similar must never be stored (Finding 2). Applies to
    # both the new-person branch below and the backfill-on-existing branch.
    profile_url_raw = (data.get("profile_url") or "").strip()
    profile_url = profile_url_raw[:1000] if profile_url_raw.startswith("http") else None
    existing = db.scalar(
        select(Person).where(
            func.lower(Person.name) == name.lower(),
            func.lower(Person.company) == company.lower(),
        )
    )
    if existing is not None:
        merged = list(existing.evidence_urls or [])
        if evidence and evidence not in merged:
            merged.append(evidence)
            existing.evidence_urls = merged
        if not existing.headline and data.get("headline"):
            existing.headline = data["headline"][:300]
        if not existing.profile_url and profile_url:
            existing.profile_url = profile_url
        return existing
    person = Person(
        name=name[:200],
        headline=(data.get("headline") or "")[:300] or None,
        company=company,
        location=(data.get("location") or "")[:200] or None,
        person_type=data.get("person_type") if data.get("person_type") in
            ("alumni", "engineer", "recruiter", "manager") else "other",
        profile_url=profile_url,
        evidence_urls=[evidence] if evidence else [],
        source=source,
        summary=(data.get("reason") or "")[:500] or None,
    )
    db.add(person)
    return person


def discover(db: Session, target: NetworkTarget, limit: int = 5, force: bool = False) -> list[Person]:
    """One web-grounded search for one target (subscription CLI first, metered
    API as fallback — see engine.generate_search). Returns the upserted people;
    [] without any engine call when inside the cooldown window."""
    if not force and target.discovered_at is not None:
        last = target.discovered_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last < COOLDOWN:
            return []
    role = f" working in {target.role_type}" if target.role_type else ""
    profile = autofill_service.load_profile(db)
    edu = ", ".join(
        v for v in (profile.get("school"), profile.get("major"), profile.get("degree")) if v
    )
    applicant = f"a student ({edu})" if edu else "a student"
    text, _sources = engine.generate_search(
        DISCOVER_SYSTEM,
        f"Find up to {limit} people at {target.company}{role} worth a cold "
        f"outreach from {applicant} seeking internships.",
        max_uses=8,
    )
    people = []
    for data in _parse_people(text)[:limit]:
        p = _upsert_person(db, data, target.company, "web_search")
        if p is not None:
            people.append(p)
    target.discovered_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("discover: %s -> %d people (target=%s)", target.company, len(people), target.id)
    return people


ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "match_signals": {"type": "array", "items": {"type": "object", "properties": {
            "signal": {"type": "string"}, "detail": {"type": "string"}},
            "required": ["signal", "detail"], "additionalProperties": False}},
        "summary": {"type": "string"},
        "connection_note": {"type": "string"},
        "followup": {"type": "string"},
    },
    "required": ["match_signals", "summary", "connection_note", "followup"],
    "additionalProperties": False,
}

ENRICH_SYSTEM = (
    """You prepare cold-outreach material for a student seeking internships
(their profile is provided below).

Given a person at a target company plus the applicant's profile and memories:
1. match_signals: the REAL overlaps only (school, clubs, employer, location,
   field). Never invent an overlap.
2. summary: who they are and why they're worth contacting, <=120 words.
3. connection_note: a LinkedIn connection request note, UNDER 300 characters,
   first person as the applicant, leading with the strongest shared signal.
4. followup: a message for after they accept, <=120 words, one concrete
   question, no referral ask.

"""
    + writing.AI_WRITING_RULES
)


def enrich(db: Session, person: Person) -> Person:
    """Best-effort: fills signals/summary/drafts; on failure the row is
    returned unchanged (retryable from the UI)."""
    try:
        system = ENRICH_SYSTEM
        voice = voice_service.voice_context(db)
        if voice:
            system += f"\n\n{voice}"
        profile = autofill_service.load_profile(db)
        ctx = memory_service.retrieve_context(
            db, f"{person.company} {person.headline or ''}", k_seeds=5
        )
        parts = [
            f"PERSON:\nname: {person.name}\nheadline: {person.headline or '?'}\n"
            f"company: {person.company}\nlocation: {person.location or '?'}\n"
            f"type: {person.person_type}\nevidence: {', '.join(person.evidence_urls or [])}",
            f"APPLICANT'S PROFILE:\n{profile}",
        ]
        if ctx.entries:
            parts.append(f"APPLICANT'S RELEVANT MEMORIES:\n{ctx.markdown}")
        payload = engine.generate_json(system, "\n\n---\n\n".join(parts), ENRICH_SCHEMA)
        # Index required keys directly (not .get(..., default)): the CLI engine
        # path doesn't schema-validate, so a valid-JSON-but-incomplete reply
        # must raise here (KeyError -> caught below -> rollback) rather than
        # silently coercing a missing key to "" -> None and committing over
        # previously-good fields. An empty match_signals LIST is legitimate
        # (zero real overlaps); only key ABSENCE is a failure.
        person.match_signals = payload["match_signals"]
        person.summary = writing.scrub(payload["summary"]) or None
        person.connection_note = writing.scrub(payload["connection_note"])[:300] or None
        person.followup = writing.scrub(payload["followup"]) or None
        db.commit()
    except Exception as e:
        logger.warning("enrich failed for person %s: %s", person.id, e)
        db.rollback()
    return person
