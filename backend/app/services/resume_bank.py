"""Resume bank rules shared by the HTTP API and the MCP server.

Updates never mutate — every edit creates a new version linked via parent_id,
compiled immediately so a bad edit can never become the head of a lineage.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Resume
from app.services.latex import CompileError, compile_tex, pdf_page_count


def create_version(db: Session, base: Resume, tex_source: str, name: str | None = None) -> Resume:
    """New compiled version of `base`. Raises CompileError (db rolled back) on failure."""
    version = Resume(
        name=name or base.name,
        job_type=base.job_type,
        tex_source=tex_source,
        parent_id=base.id,
    )
    return compile_and_store(db, version)


def compile_and_store(db: Session, resume: Resume) -> Resume:
    db.add(resume)
    db.flush()
    try:
        pdf_path = compile_tex(
            resume.tex_source, settings.files_dir / "resumes", f"resume_{resume.id}"
        )
    except CompileError:
        db.rollback()
        raise
    resume.pdf_path = str(pdf_path)
    resume.page_count = pdf_page_count(pdf_path)
    db.commit()
    return resume


def latest_versions(db: Session, job_type: str | None = None, include_pdf_only: bool = False) -> list[Resume]:
    """Heads of each lineage: entries no newer version points back to.

    By default, returns LaTeX-only heads (tex_source is not None). Pass
    include_pdf_only=True to include PDF-only resumes (tex_source is None).
    """
    query = select(Resume)
    if not include_pdf_only:
        query = query.where(Resume.tex_source.is_not(None))
    if job_type:
        query = query.where(Resume.job_type == job_type)
    resumes = db.scalars(query).all()
    child_parents = {r.parent_id for r in resumes if r.parent_id is not None}
    return [r for r in resumes if r.id not in child_parents]


def lineage(db: Session, resume: Resume) -> list[Resume]:
    """Every version in this resume's family: the target, all ancestors via
    parent_id, and all descendants (including branches)."""
    rows = db.scalars(select(Resume)).all()
    by_id = {r.id: r for r in rows}
    children: dict[int, list[Resume]] = {}
    for r in rows:
        if r.parent_id is not None:
            children.setdefault(r.parent_id, []).append(r)
    family: dict[int, Resume] = {}
    stack = [resume]
    while stack:
        r = stack.pop()
        if r.id in family:
            continue
        family[r.id] = r
        parent = by_id.get(r.parent_id) if r.parent_id is not None else None
        if parent is not None:
            stack.append(parent)
        stack.extend(children.get(r.id, []))
    return list(family.values())


@dataclass
class BulkEditOutcome:
    id: int
    name: str
    status: str  # updated | compile_failed
    new_id: int | None = None
    error: str | None = None


def bulk_find_replace(
    db: Session, find: str, replace: str, job_type: str | None = None
) -> list[BulkEditOutcome]:
    """Literal find/replace across lineage heads, one new version per match."""
    results: list[BulkEditOutcome] = []
    for head in latest_versions(db, job_type):
        if find not in (head.tex_source or ""):
            continue
        try:
            version = create_version(db, head, head.tex_source.replace(find, replace))
        except CompileError as e:
            results.append(
                BulkEditOutcome(id=head.id, name=head.name, status="compile_failed", error=str(e))
            )
            continue
        results.append(
            BulkEditOutcome(id=head.id, name=head.name, status="updated", new_id=version.id)
        )
    return results
