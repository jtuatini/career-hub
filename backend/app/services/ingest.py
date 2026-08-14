"""Ingestion: turn uploaded documents (old resumes, essays, answer dumps) into
structured brain entries via Claude. Only the document text is sent to the API."""

from io import BytesIO

from sqlalchemy.orm import Session

from app.db.models import MemoryEntry, MemoryType
from app.services import memory as memory_service
from app.services.engine import generate_json

VALID_TYPES = {t.value for t in MemoryType}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(VALID_TYPES)},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["type", "title", "content", "tags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}

SYSTEM = """\
You extract memory entries for a personal "brain" that powers internship
applications. From the document, pull distinct experiences, skills, stories,
personal facts, and preferences. Rules:
- Preserve concrete details: numbers, technologies, outcomes, names of programs.
- content is written in first person, 1-4 sentences, no embellishment beyond the
  source document.
- Short specific titles. 2-5 lowercase tags each.
- Split unrelated accomplishments into separate entries; skip boilerplate.
- Typical documents yield 3-15 entries.\
"""


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return data.decode("utf-8", errors="replace")


def ingest_text(db: Session, text: str, source: str) -> list[MemoryEntry]:
    result = generate_json(SYSTEM, f"Document to extract from:\n\n{text}", EXTRACTION_SCHEMA)
    created = []
    for item in result.get("entries", []):
        if item.get("type") not in VALID_TYPES or not item.get("title") or not item.get("content"):
            continue
        created.append(
            memory_service.create_entry(
                db,
                item["type"],
                item["title"],
                item["content"],
                item.get("tags") or [],
                source=source,
            )
        )
    return created
