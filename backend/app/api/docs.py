from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import get_db
from app.db.models import ApplySession, DocType, GeneratedDoc, Job, Resume
from app.services import auth
from app.services import autofill as autofill_service
from app.services import fit
from app.services import voice as voice_service
from app.services.atscheck import ats_report
from app.services.coverletter import build_cover_letter_tex
from app.services.latex import CompileError, compile_tex, pdf_page_count

router = APIRouter(prefix="/api/docs", tags=["docs"])


class DocDetail(BaseModel):
    id: int
    job_id: int
    doc_type: str
    approved: bool
    vetted: bool
    tex_source: str
    base_tex_source: str | None
    edits: list[dict] | None
    created_at: datetime
    body_text: str | None
    draft_text: str | None


def _get_or_404(db: Session, doc_id: int) -> GeneratedDoc:
    doc = db.get(GeneratedDoc, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


class DocListItem(BaseModel):
    id: int
    job_id: int
    doc_type: str
    approved: bool
    vetted: bool
    created_at: datetime
    company: str
    title: str
    job_status: str
    job_url: str | None


@router.get("", response_model=list[DocListItem])
def list_docs(
    limit: int = Query(10, ge=1, le=200),
    doc_type: str | None = None,
    status: Literal["draft", "unvetted", "vetted"] | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """Recent generated documents with job context. Filters serve the
    dashboard's cover-letter library; defaults preserve the old feed."""
    query = select(GeneratedDoc).order_by(
        GeneratedDoc.created_at.desc(), GeneratedDoc.id.desc()
    )
    if doc_type:
        query = query.where(GeneratedDoc.doc_type == doc_type)
    if status == "draft":
        query = query.where(GeneratedDoc.approved.is_(False))
    elif status == "unvetted":
        query = query.where(GeneratedDoc.approved.is_(True), GeneratedDoc.vetted.is_(False))
    elif status == "vetted":
        query = query.where(GeneratedDoc.vetted.is_(True))
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Job, GeneratedDoc.job_id == Job.id).where(
            or_(
                GeneratedDoc.body_text.ilike(like),
                Job.company.ilike(like),
                Job.title.ilike(like),
            )
        )
    docs = db.scalars(query.limit(limit)).all()
    return [
        DocListItem(
            id=d.id,
            job_id=d.job_id,
            doc_type=d.doc_type,
            approved=d.approved,
            vetted=d.vetted,
            created_at=d.created_at,
            company=d.job.company if d.job else "?",
            title=d.job.title if d.job else "?",
            job_status=d.job.status if d.job else "?",
            job_url=d.job.url if d.job else None,
        )
        for d in docs
    ]


@router.get("/{doc_id}", response_model=DocDetail)
def get_doc(doc_id: int, db: Session = Depends(get_db)):
    doc = _get_or_404(db, doc_id)
    base_tex = None
    if doc.base_resume_id is not None and doc.doc_type == "resume":
        base = db.get(Resume, doc.base_resume_id)
        base_tex = base.tex_source if base else None
    return DocDetail(
        id=doc.id,
        job_id=doc.job_id,
        doc_type=doc.doc_type,
        approved=doc.approved,
        vetted=doc.vetted,
        tex_source=doc.tex_source,
        base_tex_source=base_tex,
        edits=doc.edits,
        created_at=doc.created_at,
        body_text=doc.body_text,
        draft_text=doc.draft_text,
    )


@router.get("/{doc_id}/pdf")
def download_pdf(doc_id: int, db: Session = Depends(get_db)):
    doc = _get_or_404(db, doc_id)
    if not doc.pdf_path:
        raise HTTPException(status_code=404, detail="No compiled PDF for this document")
    return FileResponse(doc.pdf_path, media_type="application/pdf")


@router.get("/{doc_id}/pdf-ticket")
def pdf_ticket(doc_id: int, db: Session = Depends(get_db)):
    """Signed short-lived URL for opening this PDF in a plain browser tab."""
    _get_or_404(db, doc_id)
    path = f"/api/docs/{doc_id}/pdf"
    return {"url": f"{path}?{auth.ticket_query(path)}"}


@router.delete("/{doc_id}", status_code=204)
def delete_doc(doc_id: int, db: Session = Depends(get_db)):
    """Remove a generated document (library housekeeping). Sessions that
    referenced it are unlinked; the PDF file is removed best-effort."""
    doc = _get_or_404(db, doc_id)
    pdf = doc.pdf_path
    doc_id_val = doc.id
    db.query(ApplySession).filter(ApplySession.resume_doc_id == doc_id_val).update(
        {"resume_doc_id": None}, synchronize_session=False
    )
    db.query(ApplySession).filter(ApplySession.cover_doc_id == doc_id_val).update(
        {"cover_doc_id": None}, synchronize_session=False
    )
    db.delete(doc)
    db.commit()
    if pdf:
        Path(pdf).unlink(missing_ok=True)


@router.get("/{doc_id}/ats")
def ats_check(doc_id: int, db: Session = Depends(get_db)):
    """Local ATS-parse check + JD keyword match for a generated PDF."""
    doc = _get_or_404(db, doc_id)
    if not doc.pdf_path:
        raise HTTPException(status_code=404, detail="No compiled PDF for this document")
    return ats_report(doc.pdf_path, doc.job.jd_text if doc.job else None)


def _reject_cover_letter_or_422(doc: GeneratedDoc) -> None:
    if doc.doc_type == DocType.COVER_LETTER:
        raise HTTPException(
            status_code=422,
            detail="Cover letters are approved via /finalize, not /approve",
        )


def _reject_overflowing_resume_or_422(db: Session, doc: GeneratedDoc) -> None:
    """A tailored resume may never be approved above its base page count.
    When the base resume is gone (lineage deleted), the cap is 1 page."""
    if doc.doc_type != DocType.RESUME or not doc.pdf_path:
        return
    base = db.get(Resume, doc.base_resume_id) if doc.base_resume_id else None
    cap = (base.page_count if base else None) or 1
    pages = pdf_page_count(doc.pdf_path)
    if pages > cap:
        raise HTTPException(
            status_code=422,
            detail=f"This resume is {pages} pages (cap {cap}) — regenerate before approving",
        )


@router.post("/{doc_id}/approve")
def approve(doc_id: int, db: Session = Depends(get_db)):
    doc = _get_or_404(db, doc_id)
    _reject_cover_letter_or_422(doc)
    _reject_overflowing_resume_or_422(db, doc)
    doc.approved = True
    db.commit()
    return {"id": doc.id, "approved": doc.approved}


class BodyUpdate(BaseModel):
    body_text: str


class TexUpdate(BaseModel):
    tex_source: str


def _resume_or_422(doc: GeneratedDoc) -> None:
    if doc.doc_type != DocType.RESUME:
        raise HTTPException(
            status_code=422,
            detail="Only tailored resumes are edited as LaTeX — cover letters use /body",
        )


@router.put("/{doc_id}/tex")
def update_tex(doc_id: int, payload: TexUpdate, db: Session = Depends(get_db)):
    """Hand-edit a tailored resume's LaTeX and recompile. The wording-only rule
    binds the AI, not the user — this is their own document. Nothing persists
    unless the new source compiles; the page cap still gates approval, so an
    edit that overflows an approved resume revokes its approval."""
    doc = _get_or_404(db, doc_id)
    _resume_or_422(doc)
    if not payload.tex_source.strip():
        raise HTTPException(status_code=422, detail="LaTeX source is empty")
    warnings: list[str] = []
    fit_report = fit.estimate(payload.tex_source)
    if not fit_report.fits:
        warnings.append(fit.describe(fit_report))
    try:
        pdf_path = compile_tex(payload.tex_source, settings.files_dir / "docs", f"doc_{doc.id}")
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e
    doc.tex_source = payload.tex_source
    doc.pdf_path = str(pdf_path)
    pages = pdf_page_count(pdf_path)
    base = db.get(Resume, doc.base_resume_id) if doc.base_resume_id else None
    cap = (base.page_count if base else None) or 1
    if pages > cap:
        if doc.approved:
            doc.approved = False
            warnings.append(f"Now {pages} pages (cap {cap}) — approval revoked until it fits")
        else:
            warnings.append(f"Now {pages} pages (cap {cap}) — not approvable until it fits")
    db.commit()
    return {"id": doc.id, "page_count": pages, "approved": doc.approved, "warnings": warnings}


@router.get("/{doc_id}/fit")
def doc_fit(doc_id: int, db: Session = Depends(get_db)):
    """Deterministic line-budget estimate for the doc's current LaTeX."""
    doc = _get_or_404(db, doc_id)
    _resume_or_422(doc)
    report = fit.estimate(doc.tex_source)
    return {"lines": report.lines, "budget": report.budget, "effective_budget": report.effective_budget,
            "fits": report.fits, "sections": report.sections}


def _cover_letter_or_422(doc: GeneratedDoc) -> None:
    if doc.doc_type != DocType.COVER_LETTER:
        raise HTTPException(status_code=422, detail="Only cover letters have an editable body")


@router.put("/{doc_id}/body")
def update_body(doc_id: int, payload: BodyUpdate, db: Session = Depends(get_db)):
    doc = _get_or_404(db, doc_id)
    _cover_letter_or_422(doc)
    if doc.approved:
        raise HTTPException(status_code=409, detail="Already approved — regenerate to edit again")
    doc.body_text = payload.body_text
    db.commit()
    return {"id": doc.id, "body_text": doc.body_text}


@router.post("/{doc_id}/finalize")
def finalize(doc_id: int, db: Session = Depends(get_db)):
    """Cover letters only: compile the (possibly edited) body to PDF, approve,
    and learn from the edit — voice learning is best-effort by contract."""
    doc = _get_or_404(db, doc_id)
    _cover_letter_or_422(doc)
    if doc.approved:
        raise HTTPException(status_code=409, detail="Already finalized")
    if not doc.body_text or not doc.body_text.strip():
        raise HTTPException(status_code=422, detail="Cover letter body is empty")
    company = doc.job.company if doc.job else ""
    doc.tex_source = build_cover_letter_tex(company, doc.body_text, autofill_service.load_profile(db))
    try:
        pdf_path = compile_tex(doc.tex_source, settings.files_dir / "docs", f"doc_{doc.id}")
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e
    doc.pdf_path = str(pdf_path)
    pages = pdf_page_count(pdf_path)
    doc.approved = True
    doc.vetted = True
    db.commit()
    if doc.draft_text:
        voice_service.learn_from_edit(db, doc.draft_text, doc.body_text, "cover letter")
    return {"id": doc.id, "approved": True, "page_count": pages}


@router.post("/{doc_id}/amend")
def amend(doc_id: int, payload: BodyUpdate, db: Session = Depends(get_db)):
    """Edit an already-finalized cover letter: recompile, learn from the edit,
    and mark it vetted (exemplar-eligible). The one-click pipeline finalizes
    letters unvetted; this is the user's review path."""
    doc = _get_or_404(db, doc_id)
    _cover_letter_or_422(doc)
    if not doc.approved:
        raise HTTPException(status_code=409, detail="Not finalized yet — edit via /body and /finalize")
    if not payload.body_text.strip():
        raise HTTPException(status_code=422, detail="Cover letter body is empty")
    previous = doc.draft_text or doc.body_text
    doc.body_text = payload.body_text
    company = doc.job.company if doc.job else ""
    doc.tex_source = build_cover_letter_tex(company, doc.body_text, autofill_service.load_profile(db))
    try:
        pdf_path = compile_tex(doc.tex_source, settings.files_dir / "docs", f"doc_{doc.id}")
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e
    doc.pdf_path = str(pdf_path)
    pages = pdf_page_count(pdf_path)
    doc.vetted = True
    db.commit()
    voice_service.learn_from_edit(db, previous, doc.body_text, "cover letter")
    return {"id": doc.id, "approved": True, "vetted": True, "page_count": pages}
