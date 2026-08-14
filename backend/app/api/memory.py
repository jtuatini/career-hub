from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import MemoryEntry, MemoryLink, MemoryType
from app.services import graphlink
from app.services import ingest as ingest_service
from app.services import memory as memory_service

router = APIRouter(prefix="/api/memory", tags=["memory"])

VALID_TYPES = {t.value for t in MemoryType}


class EntryCreate(BaseModel):
    type: str
    title: str
    content: str
    tags: list[str] = []
    source: str | None = None


class EntryUpdate(BaseModel):
    type: str | None = None
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    muted: bool | None = None


class EntryOut(BaseModel):
    id: int
    type: str
    title: str
    content: str
    tags: list[str]
    source: str | None
    muted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LinkedOut(BaseModel):
    link_id: int
    relation: str | None
    entry: EntryOut


class EntryDetail(EntryOut):
    links: list[LinkedOut] = []


class LinkCreate(BaseModel):
    from_id: int
    to_id: int
    relation: str | None = None


class SearchRequest(BaseModel):
    query: str
    k: int = 5
    types: list[str] | None = None


class SearchHit(BaseModel):
    score: float
    entry: EntryOut


def _validate_type(type_: str) -> None:
    if type_ not in VALID_TYPES:
        raise HTTPException(
            status_code=422, detail=f"type must be one of: {', '.join(sorted(VALID_TYPES))}"
        )


def _get_or_404(db: Session, entry_id: int) -> MemoryEntry:
    entry = db.get(MemoryEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return entry


@router.post("", status_code=201, response_model=EntryDetail)
def create_entry(payload: EntryCreate, db: Session = Depends(get_db)):
    _validate_type(payload.type)
    entry = memory_service.create_entry(
        db, payload.type, payload.title, payload.content, payload.tags, payload.source
    )
    graphlink.link_batch(db, [entry])  # best-effort: errors are collected, not raised
    return get_entry(entry.id, db)


@router.get("", response_model=list[EntryOut])
def list_entries(type: str | None = None, db: Session = Depends(get_db)):
    stmt = select(MemoryEntry).order_by(MemoryEntry.created_at.desc(), MemoryEntry.id.desc())
    if type:
        stmt = stmt.where(MemoryEntry.type == type)
    return db.scalars(stmt).all()


@router.get("/graph")
def graph(db: Session = Depends(get_db)):
    """The whole brain in one payload for the graph view."""
    entries = db.scalars(select(MemoryEntry)).all()
    links = db.scalars(select(MemoryLink)).all()
    degree: Counter[int] = Counter()
    for link in links:
        degree[link.from_id] += 1
        degree[link.to_id] += 1
    return {
        "nodes": [
            {
                "id": e.id,
                "type": e.type,
                "title": e.title,
                "muted": e.muted,
                "degree": degree[e.id],
            }
            for e in entries
        ],
        "links": [
            {"id": l.id, "from_id": l.from_id, "to_id": l.to_id, "relation": l.relation}
            for l in links
        ],
    }


@router.post("/organize")
def organize(db: Session = Depends(get_db)):
    """One-time (re-runnable) pass wiring unlinked memories into the graph."""
    return graphlink.organize(db)


@router.get("/{entry_id}", response_model=EntryDetail)
def get_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = _get_or_404(db, entry_id)
    detail = EntryDetail.model_validate(entry)
    detail.links = [
        LinkedOut(link_id=link.id, relation=link.relation, entry=EntryOut.model_validate(other))
        for link, other in memory_service.linked_entries(db, entry.id)
    ]
    return detail


@router.put("/{entry_id}", response_model=EntryOut)
def update_entry(entry_id: int, payload: EntryUpdate, db: Session = Depends(get_db)):
    if payload.type is not None:
        _validate_type(payload.type)
    entry = _get_or_404(db, entry_id)
    return memory_service.update_entry(
        db, entry, payload.title, payload.content, payload.tags, payload.type, payload.muted
    )


@router.delete("/{entry_id}", status_code=204)
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    memory_service.delete_entry(db, _get_or_404(db, entry_id))


@router.post("/links", status_code=201)
def create_link(payload: LinkCreate, db: Session = Depends(get_db)):
    try:
        link = memory_service.link_entries(db, payload.from_id, payload.to_id, payload.relation)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"id": link.id, "from_id": link.from_id, "to_id": link.to_id, "relation": link.relation}


@router.post("/ingest", status_code=201, response_model=list[EntryOut])
def ingest_document(file: UploadFile, db: Session = Depends(get_db)):
    """Upload an old resume/essay/answers doc (.pdf/.txt/.md); Claude extracts
    brain entries from it. Only the document text is sent to the API."""
    filename = file.filename or "upload"
    text = ingest_service.extract_text(filename, file.file.read()).strip()
    if len(text) < 40:
        raise HTTPException(status_code=422, detail="Couldn't extract usable text from that file")
    try:
        created = ingest_service.ingest_text(db, text, source=filename)
    except RuntimeError as e:  # missing API key, refusal
        raise HTTPException(status_code=503, detail=str(e)) from e
    graphlink.link_batch(db, created)  # best-effort: errors are collected, not raised
    return created


@router.post("/search", response_model=list[SearchHit])
def search(payload: SearchRequest, db: Session = Depends(get_db)):
    if payload.types:
        for t in payload.types:
            _validate_type(t)
    hits = memory_service.search_memory(db, payload.query, payload.k, payload.types)
    return [SearchHit(score=score, entry=EntryOut.model_validate(e)) for e, score in hits]
