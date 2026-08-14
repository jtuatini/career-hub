"""Voice training: build a style profile from the user's real writing, render it
for prompts, refine drafts against it, and learn from their edits.

critique_refine and learn_from_edit are best-effort BY CONTRACT: they must
never raise — a voice failure can never block producing a document."""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import StyleProfile, VoiceSample
from app.services.engine import generate_json
from app.services.tailor import compute_divergence

logger = logging.getLogger(__name__)

MAX_RULES = 30
MIN_EDIT_DIVERGENCE = 0.05

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {"profile": {"type": "string"}},
    "required": ["profile"],
    "additionalProperties": False,
}

PROFILE_SYSTEM = """\
You are a writing-style analyst. From the labeled samples of one author's real
writing, produce a style profile another writer could use to write AS him.
Output markdown with exactly these sections:
## Tone
## Sentence rhythm
## Vocabulary
(words and phrases he actually uses; words and clichés to avoid)
## Openings & closings
## Structural habits
## Blending the registers
(how his informal energy should carry into formal application writing)
Ground every observation in the samples — quote short fragments as evidence.
Describe the voice he HAS, not the voice you think he should have.\
"""

REFINE_SCHEMA = {
    "type": "object",
    "properties": {"revised": {"type": "string"}},
    "required": ["revised"],
    "additionalProperties": False,
}

REFINE_SYSTEM = """\
You are the author's voice editor. Given his style profile and a draft written
on his behalf, find what does NOT sound like him — generic phrasing, wrong
rhythm, vocabulary he'd never use — and rewrite those spans in his voice.
Keep every fact, claim, and name exactly as-is; stay within ±10% of the draft's
length; change only wording. Return the full revised text.\
"""

RULES_SCHEMA = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": {"type": "string"}}},
    "required": ["rules"],
    "additionalProperties": False,
}

RULES_SYSTEM = """\
The author edited an AI draft before using it. Compare draft and final and
extract 0-3 SHORT, GENERALIZABLE writing rules from what he changed (e.g.
"Opens with intent, not formality", "Cuts intensifiers"). Only rules clearly
supported by the edits; never duplicate an existing rule; return [] when the
edits are content-only (facts, not style).\
"""


def get_profile(db: Session) -> StyleProfile | None:
    return db.scalar(select(StyleProfile).order_by(StyleProfile.id).limit(1))


def build_profile(db: Session) -> StyleProfile:
    """One engine call over all samples. Rebuild replaces content, preserves
    learned_rules. Raises ValueError when there are no samples."""
    samples = db.scalars(select(VoiceSample).order_by(VoiceSample.created_at)).all()
    if not samples:
        raise ValueError("Add writing samples first — the profile is built from them")
    blocks = [
        f"=== SAMPLE {i + 1} [{s.kind}] {s.title} ===\n{s.text}"
        for i, s in enumerate(samples)
    ]
    payload = generate_json(PROFILE_SYSTEM, "\n\n".join(blocks), PROFILE_SCHEMA)
    profile = get_profile(db)
    if profile is None:
        profile = StyleProfile(content=payload["profile"])
        db.add(profile)
    else:
        profile.content = payload["profile"]
    db.commit()
    return profile


def voice_context(db: Session) -> str:
    """Prompt block for generation consumers. Empty string when unbuilt."""
    profile = get_profile(db)
    if profile is None:
        return ""
    out = f"## His voice (write exactly in this style)\n{profile.content}"
    if profile.learned_rules:
        rules = "\n".join(f"- {r['rule']}" for r in profile.learned_rules)
        out += f"\n\n## Learned corrections (from his own edits — always apply)\n{rules}"
    return out


def critique_refine(db: Session, body: str, job_context: str) -> str:
    """Second pass on cover letters. Returns the original body unchanged when
    there is no profile or on ANY failure."""
    try:
        voice = voice_context(db)
        if not voice:
            return body
        payload = generate_json(
            REFINE_SYSTEM,
            f"{voice}\n\nJOB CONTEXT: {job_context}\n\nDRAFT:\n{body}",
            REFINE_SCHEMA,
        )
        revised = (payload.get("revised") or "").strip()
        return revised or body
    except Exception as e:
        logger.warning("critique_refine failed, using raw draft: %s", e)
        return body


def learn_from_edit(db: Session, draft: str, final: str, context: str) -> dict:
    """Extract style rules from the diff between the AI draft and his final.
    Best-effort: never raises."""
    try:
        profile = get_profile(db)
        if profile is None or compute_divergence(draft, final) < MIN_EDIT_DIVERGENCE:
            return {"skipped": True}
        existing = "\n".join(f"- {r['rule']}" for r in profile.learned_rules) or "(none)"
        payload = generate_json(
            RULES_SYSTEM,
            f"CONTEXT: {context}\n\nEXISTING RULES:\n{existing}\n\n"
            f"AI DRAFT:\n{draft}\n\nHIS FINAL VERSION:\n{final}",
            RULES_SCHEMA,
        )
        today = date.today().isoformat()
        new_rules = [
            {"date": today, "rule": r.strip()}
            for r in payload.get("rules", [])
            if isinstance(r, str) and r.strip()
        ][:3]
        if not new_rules:
            return {"added": 0}
        profile.learned_rules = (list(profile.learned_rules) + new_rules)[-MAX_RULES:]
        db.commit()
        return {"added": len(new_rules)}
    except Exception as e:
        logger.warning("learn_from_edit failed (non-blocking): %s", e)
        return {"error": str(e)}
