"""The brain: memory web CRUD, link graph, and local embedding retrieval.

Consumers: the memory/qa API routers, the brain MCP tools, and (later) the
tailoring pipeline's context retrieval.
"""

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import MemoryEntry, MemoryLink, QABankEntry
from app.services import embeddings

# Two node families: content nodes carry prose; entity hubs give the graph shape.
ENTITY_TYPES = {"skill", "project", "company", "trait"}
CONTENT_TYPES = {"experience", "story", "personal", "preference"}
RELATIONS = {"demonstrates", "used", "built", "worked_at", "part_of", "led_to", "related"}


def create_entry(
    db: Session,
    type: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    source: str | None = None,
) -> MemoryEntry:
    entry = MemoryEntry(
        type=type,
        title=title,
        content=content,
        tags=tags or [],
        source=source,
        embedding=embeddings.embed_text(f"{title}\n{content}"),
    )
    db.add(entry)
    db.commit()
    return entry


def update_entry(
    db: Session,
    entry: MemoryEntry,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    type: str | None = None,
    muted: bool | None = None,
) -> MemoryEntry:
    entry.title = title if title is not None else entry.title
    entry.content = content if content is not None else entry.content
    entry.tags = tags if tags is not None else entry.tags
    entry.type = type if type is not None else entry.type
    entry.muted = muted if muted is not None else entry.muted
    entry.embedding = embeddings.embed_text(f"{entry.title}\n{entry.content}")
    db.commit()
    return entry


def delete_entry(db: Session, entry: MemoryEntry) -> None:
    db.query(MemoryLink).filter(
        or_(MemoryLink.from_id == entry.id, MemoryLink.to_id == entry.id)
    ).delete()
    db.delete(entry)
    db.commit()


def link_entries(db: Session, from_id: int, to_id: int, relation: str | None = None) -> MemoryLink:
    if from_id == to_id:
        raise ValueError("Cannot link an entry to itself")
    if relation is not None and relation not in RELATIONS:
        raise ValueError(f"relation must be one of: {', '.join(sorted(RELATIONS))}")
    for entry_id in (from_id, to_id):
        if db.get(MemoryEntry, entry_id) is None:
            raise ValueError(f"Memory entry {entry_id} not found")
    existing = db.scalar(
        select(MemoryLink).where(MemoryLink.from_id == from_id, MemoryLink.to_id == to_id)
    )
    if existing:
        existing.relation = relation or existing.relation
        db.commit()
        return existing
    link = MemoryLink(from_id=from_id, to_id=to_id, relation=relation)
    db.add(link)
    db.commit()
    return link


def linked_entries(db: Session, entry_id: int) -> list[tuple[MemoryLink, MemoryEntry]]:
    """Neighbors in the web, either direction."""
    links = db.scalars(
        select(MemoryLink).where(
            or_(MemoryLink.from_id == entry_id, MemoryLink.to_id == entry_id)
        )
    ).all()
    out = []
    for link in links:
        other_id = link.to_id if link.from_id == entry_id else link.from_id
        other = db.get(MemoryEntry, other_id)
        if other is not None:
            out.append((link, other))
    return out


def search_memory(
    db: Session, query: str, k: int = 5, types: list[str] | None = None
) -> list[tuple[MemoryEntry, float]]:
    stmt = select(MemoryEntry).where(
        MemoryEntry.embedding.is_not(None), MemoryEntry.muted.is_(False)
    )
    if types:
        stmt = stmt.where(MemoryEntry.type.in_(types))
    entries = db.scalars(stmt).all()
    scores = embeddings.cosine_scores(
        embeddings.embed_text(query), [e.embedding for e in entries]
    )
    ranked = sorted(zip(entries, scores), key=lambda pair: pair[1], reverse=True)
    return ranked[:k]


def save_qa(
    db: Session,
    question: str,
    answer: str,
    tags: list[str] | None = None,
    job_id: int | None = None,
) -> QABankEntry:
    qa = QABankEntry(
        question=question,
        answer=answer,
        tags=tags or [],
        job_id=job_id,
        embedding=embeddings.embed_text(question),
    )
    db.add(qa)
    db.commit()
    return qa


def search_qa(db: Session, query: str, k: int = 5) -> list[tuple[QABankEntry, float]]:
    entries = db.scalars(
        select(QABankEntry).where(QABankEntry.embedding.is_not(None))
    ).all()
    scores = embeddings.cosine_scores(
        embeddings.embed_text(query), [e.embedding for e in entries]
    )
    ranked = sorted(zip(entries, scores), key=lambda pair: pair[1], reverse=True)
    return ranked[:k]


@dataclass
class RetrievedContext:
    """A graph-expanded context bundle for LLM consumers."""

    markdown: str
    seeds: list[tuple[MemoryEntry, float]] = field(default_factory=list)
    entries: list[MemoryEntry] = field(default_factory=list)


def _render_entry(db: Session, entry: MemoryEntry) -> str:
    rels = []
    for link, other in linked_entries(db, entry.id):
        if other.muted:
            continue
        arrow = (
            f"{link.relation or 'related'} → {other.type}: {other.title}"
            if link.from_id == entry.id
            else f"{other.type}: {other.title} → {link.relation or 'related'}"
        )
        rels.append(arrow)
    rel_line = f"\n[{'; '.join(rels)}]" if rels else ""
    return f"### {entry.type.capitalize()}: {entry.title}{rel_line}\n{entry.content}"


def retrieve_context(
    db: Session, query: str, k_seeds: int = 5, cap: int = 30
) -> RetrievedContext:
    """Embedding seeds, expanded 1 hop — and a 2nd hop through entity hubs, so
    a hub drags in everything else that proves it. Muted entries never appear.
    Generous by design: the brain is personal-scale, so err toward more context.
    """
    seeds = search_memory(db, query, k=k_seeds)
    ordered: dict[int, MemoryEntry] = {e.id: e for e, _ in seeds}
    hubs: dict[int, MemoryEntry] = {
        e.id: e for e in ordered.values() if e.type in ENTITY_TYPES
    }
    for entry, _ in seeds:
        for _link, other in linked_entries(db, entry.id):
            if other.muted:
                continue
            ordered.setdefault(other.id, other)
            if other.type in ENTITY_TYPES:
                hubs.setdefault(other.id, other)
    for hub in hubs.values():
        for _link, other in linked_entries(db, hub.id):
            if not other.muted:
                ordered.setdefault(other.id, other)
    entries = list(ordered.values())[:cap]
    markdown = "\n\n".join(_render_entry(db, e) for e in entries)
    return RetrievedContext(markdown=markdown, seeds=seeds, entries=entries)
