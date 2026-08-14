from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import DocType, GeneratedDoc, Job, Resume
from app.services import ats_scan as ats_service
from app.services import tailor_flow
from app.services.coverletter import draft_cover_letter
from app.services.latex import CompileError

router = APIRouter(prefix="/api/generate", tags=["generate"])


class TailorRequest(BaseModel):
    resume_id: int
    job_id: int | None = None
    company: str | None = None
    title: str | None = None
    url: str | None = None
    jd_text: str | None = None


class RetailorRequest(BaseModel):
    doc_id: int


class CoverLetterRequest(BaseModel):
    job_id: int
    resume_id: int | None = None


class GenerateOut(BaseModel):
    id: int
    job_id: int
    doc_type: str
    approved: bool
    page_count: int
    applied_edits: list[dict]
    rejected_edits: list[dict]
    warnings: list[str]
    divergence: float | None = None
    body_text: str | None = None


def _resolve_job(payload: TailorRequest, db: Session) -> Job:
    if payload.job_id is not None:
        job = db.get(Job, payload.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job
    if not (payload.company and payload.title and payload.jd_text):
        raise HTTPException(
            status_code=422,
            detail="Provide job_id, or company + title + jd_text to create a job",
        )
    job = Job(
        company=payload.company, title=payload.title, url=payload.url, jd_text=payload.jd_text
    )
    db.add(job)
    db.flush()
    return job


@router.post("/tailor", status_code=201, response_model=GenerateOut)
def tailor(payload: TailorRequest, db: Session = Depends(get_db)):
    resume = db.get(Resume, payload.resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    if resume.tex_source is None:
        raise HTTPException(
            status_code=422,
            detail="This resume has no LaTeX source (PDF-only) and cannot be tailored",
        )
    job = _resolve_job(payload, db)
    db.commit()  # Commit the Job row so it survives doc rollback
    jd_text = payload.jd_text or job.jd_text
    if not jd_text:
        raise HTTPException(status_code=422, detail="Job has no description text")

    try:
        outcome = tailor_flow.tailor_to_doc(db, resume, job, jd_text)
    except tailor_flow.PageOverflowError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e
    return GenerateOut(
        id=outcome.doc.id,
        job_id=job.id,
        doc_type=outcome.doc.doc_type,
        approved=outcome.doc.approved,
        page_count=outcome.pages,
        applied_edits=outcome.applied,
        rejected_edits=outcome.rejected,
        warnings=outcome.warnings,
        divergence=outcome.divergence,
    )


@router.post("/retailor", status_code=201, response_model=GenerateOut)
def retailor(payload: RetailorRequest, db: Session = Depends(get_db)):
    """Re-run tailoring for a doc's job, guided by its done ATS scans.
    Produces a NEW GeneratedDoc for the same job."""
    doc = db.get(GeneratedDoc, payload.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    job = db.get(Job, doc.job_id)
    if job is None or not job.jd_text:
        raise HTTPException(status_code=409, detail="No job description stored for this doc's job")
    resume = db.get(Resume, doc.base_resume_id) if doc.base_resume_id else None
    if resume is None or not resume.tex_source:
        raise HTTPException(status_code=409, detail="No LaTeX base resume recorded for this doc")
    guidance = ats_service.scan_guidance(db, doc.id)
    if guidance is None:
        raise HTTPException(
            status_code=409, detail="Run at least one ATS scan first — re-tailor uses its findings"
        )

    try:
        outcome = tailor_flow.tailor_to_doc(db, resume, job, job.jd_text, guidance=guidance)
    except tailor_flow.PageOverflowError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e
    return GenerateOut(
        id=outcome.doc.id,
        job_id=job.id,
        doc_type=outcome.doc.doc_type,
        approved=outcome.doc.approved,
        page_count=outcome.pages,
        applied_edits=outcome.applied,
        rejected_edits=outcome.rejected,
        warnings=outcome.warnings,
        divergence=outcome.divergence,
    )


@router.post("/cover-letter", status_code=201, response_model=GenerateOut)
def cover_letter(payload: CoverLetterRequest, db: Session = Depends(get_db)):
    job = db.get(Job, payload.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.jd_text:
        raise HTTPException(status_code=422, detail="Job has no description text")

    resume_context = ""
    if payload.resume_id is not None:
        resume = db.get(Resume, payload.resume_id)
        if resume is None:
            raise HTTPException(status_code=404, detail="Resume not found")
        resume_context = f"\n\nRESUME (.tex source):\n{resume.tex_source}"

    body = draft_cover_letter(db, job, resume_context)
    doc = GeneratedDoc(
        job_id=job.id,
        base_resume_id=payload.resume_id,
        doc_type=DocType.COVER_LETTER,
        tex_source="",
        draft_text=body,
        body_text=body,
    )
    db.add(doc)
    db.commit()
    return GenerateOut(
        id=doc.id,
        job_id=job.id,
        doc_type=doc.doc_type,
        approved=doc.approved,
        page_count=0,
        applied_edits=[],
        rejected_edits=[],
        warnings=[],
        divergence=None,
        body_text=doc.body_text,
    )
