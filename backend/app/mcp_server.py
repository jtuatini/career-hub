"""Stdio MCP server exposing the resume bank and job tracker to AI CLI sessions
(Claude Code, Codex, Antigravity).

Spawned by the embedded terminal (and mountable from any external session):
    uv run --project backend python -m app.mcp_server

Every write goes through the same service layer as the UI — versioned, compiled,
validated.

COPILOT_MCP_READONLY=1 registers only the read tools — used by headless
brainstorm sessions on CLIs that have no per-tool allowlist flag, so a
brainstorm can never mutate the brain or the resume bank.
"""

import os
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

from app.db.base import SessionLocal
from app.db.models import Job, MemoryEntry, MemoryType, Resume
from app.services import memory as memory_service
from app.services import resume_bank
from app.services.latex import CompileError

mcp = FastMCP("application-copilot")

READONLY = os.environ.get("COPILOT_MCP_READONLY") == "1"


def writable_tool(fn):
    """Register fn as an MCP tool only in read-write mode."""
    return fn if READONLY else mcp.tool()(fn)


@contextmanager
def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _resume_summary(r: Resume, heads: set[int] | None = None) -> dict:
    out = {
        "id": r.id,
        "name": r.name,
        "job_type": r.job_type,
        "page_count": r.page_count,
        "parent_id": r.parent_id,
        "has_tex": r.tex_source is not None,
    }
    if heads is not None:
        out["is_latest_version"] = r.id in heads
    return out


@mcp.tool()
def list_resumes(job_type: str | None = None) -> list[dict]:
    """List resumes in the bank. Edit only entries with is_latest_version=true;
    older versions are history. has_tex=false entries are PDF-only and not editable."""
    with _db() as db:
        query = db.query(Resume)
        if job_type:
            query = query.filter(Resume.job_type == job_type)
        heads = {r.id for r in resume_bank.latest_versions(db, job_type)}
        return [_resume_summary(r, heads) for r in query.order_by(Resume.id).all()]


@mcp.tool()
def get_resume_tex(resume_id: int) -> str:
    """Full LaTeX source of a resume."""
    with _db() as db:
        resume = db.get(Resume, resume_id)
        if resume is None:
            raise ValueError(f"Resume {resume_id} not found")
        if resume.tex_source is None:
            raise ValueError(f"Resume {resume_id} is PDF-only (no LaTeX source)")
        return resume.tex_source


@writable_tool
def update_resume_tex(resume_id: int, tex_source: str, name: str | None = None) -> dict:
    """Save an edited resume as a new version (the bank never mutates in place).
    Compiles immediately; on LaTeX errors nothing is saved and the compiler
    output is returned so you can fix and retry."""
    with _db() as db:
        base = db.get(Resume, resume_id)
        if base is None:
            raise ValueError(f"Resume {resume_id} not found")
        try:
            version = resume_bank.create_version(db, base, tex_source, name=name)
        except CompileError as e:
            return {"status": "compile_failed", "error": str(e)}
        return {"status": "updated", **_resume_summary(version)}


@writable_tool
def bulk_find_replace(find: str, replace: str, job_type: str | None = None) -> list[dict]:
    """Literal find/replace across the latest version of every LaTeX resume
    (optionally scoped to a job_type). Creates a new compiled version per match;
    reports per-resume success or compiler errors."""
    with _db() as db:
        outcomes = resume_bank.bulk_find_replace(db, find, replace, job_type)
        return [vars(o) for o in outcomes]


@mcp.tool()
def list_jobs(status: str | None = None) -> list[dict]:
    """List tracked jobs (status: saved/applied/oa/interview/offer/rejected/withdrawn)."""
    with _db() as db:
        query = db.query(Job)
        if status:
            query = query.filter(Job.status == status)
        return [
            {"id": j.id, "company": j.company, "title": j.title, "status": j.status, "url": j.url}
            for j in query.order_by(Job.id).all()
        ]


@mcp.tool()
def get_job(job_id: int) -> dict:
    """Full job record including the job-description text."""
    with _db() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        return {
            "id": job.id,
            "company": job.company,
            "title": job.title,
            "url": job.url,
            "status": job.status,
            "deadline": job.deadline.isoformat() if job.deadline else None,
            "notes": job.notes,
            "jd_text": job.jd_text,
        }


def _entry_out(entry: MemoryEntry, score: float | None = None) -> dict:
    out = {
        "id": entry.id,
        "type": entry.type,
        "title": entry.title,
        "content": entry.content,
        "tags": entry.tags,
    }
    if score is not None:
        out["score"] = round(score, 4)
    return out


@mcp.tool()
def search_memory(query: str, k: int = 5, types: list[str] | None = None) -> list[dict]:
    """Search the brain (the user's memory web: experiences, skills, stories, personal
    facts, preferences) by meaning. Use this to find real material — never invent
    experiences. types filters to a subset of: experience, skill, story, personal,
    preference."""
    with _db() as db:
        return [_entry_out(e, score) for e, score in memory_service.search_memory(db, query, k, types)]


@mcp.tool()
def get_entry(entry_id: int) -> dict:
    """A memory entry with its linked neighbors in the web."""
    with _db() as db:
        entry = db.get(MemoryEntry, entry_id)
        if entry is None:
            raise ValueError(f"Memory entry {entry_id} not found")
        out = _entry_out(entry)
        out["links"] = [
            {"relation": link.relation, **_entry_out(other)}
            for link, other in memory_service.linked_entries(db, entry_id)
        ]
        return out


@writable_tool
def add_entry(
    type: str, title: str, content: str, tags: list[str] | None = None
) -> dict:
    """Save a new memory to the brain. type: experience | skill | story | personal |
    preference | project | company | trait. Only save things the user actually said or approved — the brain must
    stay true."""
    if type not in {t.value for t in MemoryType}:
        raise ValueError(f"type must be one of: {', '.join(t.value for t in MemoryType)}")
    with _db() as db:
        entry = memory_service.create_entry(db, type, title, content, tags, source="mcp")

        from app.services import graphlink

        graphlink.link_batch(db, [entry])  # best-effort auto-linking
        return _entry_out(entry)


@writable_tool
def link_entries(from_id: int, to_id: int, relation: str | None = None) -> dict:
    """Connect two memories in the web (e.g. a story to the skill it demonstrates).
    relation must be one of: demonstrates, used, built, worked_at, part_of, led_to, related."""
    with _db() as db:
        link = memory_service.link_entries(db, from_id, to_id, relation)
        return {"id": link.id, "from_id": link.from_id, "to_id": link.to_id, "relation": link.relation}


@mcp.tool()
def search_qa(query: str, k: int = 5) -> list[dict]:
    """Search past supplemental-application answers by question meaning. Reuse and
    adapt past answers — they are in the user's approved voice."""
    with _db() as db:
        return [
            {
                "id": qa.id,
                "question": qa.question,
                "answer": qa.answer,
                "times_used": qa.times_used,
                "score": round(score, 4),
            }
            for qa, score in memory_service.search_qa(db, query, k)
        ]


@writable_tool
def save_qa_answer(
    question: str, answer: str, tags: list[str] | None = None, job_id: int | None = None
) -> dict:
    """Save an approved supplemental-question answer to the bank so future
    applications can reuse it. Only save answers the user approved."""
    with _db() as db:
        qa = memory_service.save_qa(db, question, answer, tags, job_id)
        return {"id": qa.id, "question": qa.question}


if __name__ == "__main__":
    mcp.run()
