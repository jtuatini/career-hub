"""Auto-linking: the engine reads memories and wires them to entity hubs
(skills, projects, companies, traits), creating hubs that don't exist yet.
Best-effort by design — a linking failure must never block a save."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MemoryEntry, MemoryLink
from app.services import memory as memory_service
from app.services.engine import generate_json
from app.services.memory import CONTENT_TYPES, ENTITY_TYPES, RELATIONS

logger = logging.getLogger(__name__)

LINK_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "integer"},
                    "entities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "kind": {"type": "string", "enum": sorted(ENTITY_TYPES)},
                                "relation": {"type": "string", "enum": sorted(RELATIONS)},
                                "description": {"type": "string"},
                            },
                            "required": ["name", "kind", "relation", "description"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["entry_id", "entities"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM = """\
You maintain a hub-and-spoke knowledge graph of a student's application material.
For each memory entry, list the entity hubs it genuinely involves: skills,
projects, companies/organizations, and traits (e.g. leadership, persistence).
Rules:
- Prefer EXISTING HUBS when one matches (match by meaning, not exact spelling);
  use its exact listed name. Invent a new hub only when nothing listed fits.
- 1-5 entities per entry; only what the entry clearly evidences. Short names
  ("Python", not "the Python programming language"). description: one factual
  line for new hubs, empty string for existing ones.
- relation is from the entry to the entity: demonstrates (trait/skill shown),
  used (tool/skill applied), built (thing created), worked_at (org),
  part_of (larger effort), led_to (outcome), related (anything else).\
"""


def _render_entry(entry: MemoryEntry) -> str:
    return f"ENTRY id={entry.id} [{entry.type}] {entry.title}\n{entry.content}"


def _apply_entities(db: Session, entry: MemoryEntry, entities: list[dict],
                    by_key: dict[tuple[str, str], MemoryEntry]) -> tuple[int, int]:
    hubs_created = links_created = 0
    for item in entities:
        kind = item.get("kind")
        name = (item.get("name") or "").strip()
        if kind not in ENTITY_TYPES or not name:
            continue
        relation = item.get("relation") if item.get("relation") in RELATIONS else "related"
        hub = by_key.get((kind, name.lower()))
        if hub is None:
            hub = memory_service.create_entry(
                db, kind, name, item.get("description") or name, source="auto-link"
            )
            by_key[(kind, name.lower())] = hub
            hubs_created += 1
        if hub.id == entry.id:
            continue
        memory_service.link_entries(db, entry.id, hub.id, relation)
        links_created += 1
    return hubs_created, links_created


def link_batch(db: Session, entries: list[MemoryEntry], batch_size: int = 8) -> dict:
    result = {"entries_processed": 0, "hubs_created": 0, "links_created": 0, "errors": []}
    if not entries:
        return result
    for start in range(0, len(entries), batch_size):
        batch = entries[start : start + batch_size]
        hubs = db.scalars(
            select(MemoryEntry).where(
                MemoryEntry.type.in_(ENTITY_TYPES), MemoryEntry.muted.is_(False)
            )
        ).all()
        by_key = {(h.type, h.title.lower()): h for h in hubs}
        hub_list = "\n".join(f"- {h.type}: {h.title}" for h in hubs) or "(none yet)"
        user_content = (
            "EXISTING HUBS:\n" + hub_list + "\n\nMEMORY ENTRIES:\n\n"
            + "\n\n".join(_render_entry(e) for e in batch)
        )
        # The whole batch -- engine call AND result application -- is inside this
        # try. A shape-invalid payload (results not a list, entities items not
        # dicts, etc.) must be caught here too, not just engine failures: this
        # is best-effort by design, so a malformed payload must never escape
        # and 500 the caller after entries were already committed.
        try:
            payload = generate_json(SYSTEM, user_content, LINK_SCHEMA)
            by_id = {e.id: e for e in batch}
            for item in payload.get("results", []):
                entry = by_id.get(item.get("entry_id"))
                if entry is None:
                    continue
                created, linked = _apply_entities(db, entry, item.get("entities") or [], by_key)
                result["hubs_created"] += created
                result["links_created"] += linked
                result["entries_processed"] += 1
        except Exception as e:
            logger.warning("auto-link batch failed: %s", e)
            result["errors"].append(str(e))
            continue
    return result


def auto_link(db: Session, entry: MemoryEntry) -> dict:
    return link_batch(db, [entry])


def organize(db: Session) -> dict:
    """One pass over content entries that have no links yet."""
    linked_ids = {l.from_id for l in db.scalars(select(MemoryLink)).all()} | {
        l.to_id for l in db.scalars(select(MemoryLink)).all()
    }
    candidates = [
        e
        for e in db.scalars(
            select(MemoryEntry).where(
                MemoryEntry.type.in_(CONTENT_TYPES), MemoryEntry.muted.is_(False)
            )
        ).all()
        if e.id not in linked_ids
    ]
    return link_batch(db, candidates)
