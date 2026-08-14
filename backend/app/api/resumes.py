import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import get_db
from app.db.models import GeneratedDoc, ImportSession, Resume
from app.services import resume_bank, resume_import
from app.services.latex import CompileError, pdf_page_count

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


class ResumeCreate(BaseModel):
    name: str
    job_type: str
    tex_source: str


class ResumeUpdate(BaseModel):
    tex_source: str
    name: str | None = None


class ResumeOut(BaseModel):
    id: int
    name: str
    job_type: str
    page_count: int | None
    parent_id: int | None
    created_at: datetime
    version_count: int = 1

    model_config = {"from_attributes": True}


class ResumeDetail(ResumeOut):
    tex_source: str | None


class BulkEditRequest(BaseModel):
    find: str
    replace: str
    job_type: str | None = None


class BulkEditResult(BaseModel):
    id: int
    name: str
    status: str  # updated | compile_failed
    new_id: int | None = None
    error: str | None = None


def _compile_and_store(db: Session, resume: Resume) -> Resume:
    try:
        return resume_bank.compile_and_store(db, resume)
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e


@router.post("", status_code=201, response_model=ResumeOut)
def create_resume(payload: ResumeCreate, db: Session = Depends(get_db)):
    resume = Resume(name=payload.name, job_type=payload.job_type, tex_source=payload.tex_source)
    return _compile_and_store(db, resume)


@router.get("", response_model=list[ResumeOut])
def list_resumes(job_type: str | None = None, db: Session = Depends(get_db)):
    heads = resume_bank.latest_versions(db, job_type, include_pdf_only=True)
    heads = sorted(heads, key=lambda r: (r.created_at, r.id), reverse=True)
    return [
        ResumeOut(
            id=r.id,
            name=r.name,
            job_type=r.job_type,
            page_count=r.page_count,
            parent_id=r.parent_id,
            created_at=r.created_at,
            version_count=len(resume_bank.lineage(db, r)),
        )
        for r in heads
    ]


def _get_or_404(db: Session, resume_id: int) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


class ImportSessionOut(BaseModel):
    id: int
    status: str
    stage: str
    progress: float
    error: str | None
    resume_id: int | None
    report: dict | None = None
    rounds: int = 0


def _import_or_404(db: Session, session_id: int) -> ImportSession:
    s = db.get(ImportSession, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Import session not found")
    return s


@router.post("/import-pdf", status_code=201)
def import_pdf(
    file: UploadFile,
    name: str = Form(...),
    job_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """Start a vetted PDF→LaTeX import. The pipeline runs in a background
    thread; poll GET /import-sessions/{id}."""
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=422, detail="Upload a PDF file")
    try:
        s = resume_import.create_import(
            db, file.filename or "resume.pdf", file.file.read(), name, job_type
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    resume_import.start_run(s.id)
    return {"id": s.id}


@router.get("/import-sessions/{session_id}", response_model=ImportSessionOut)
def import_session_status(session_id: int, db: Session = Depends(get_db)):
    s = _import_or_404(db, session_id)
    return ImportSessionOut(
        id=s.id, status=s.status, stage=s.stage, progress=s.progress,
        error=s.error, resume_id=s.resume_id,
        report=s.state.get("report"), rounds=s.state.get("rounds", 0),
    )


@router.get("/import-sessions/{session_id}/pdf")
def import_session_pdf(session_id: int, db: Session = Depends(get_db)):
    s = _import_or_404(db, session_id)
    path = s.state.get("candidate_pdf_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="No candidate PDF yet")
    return FileResponse(path, media_type="application/pdf")


@router.post("/import-sessions/{session_id}/accept", response_model=ResumeOut)
def import_session_accept(session_id: int, db: Session = Depends(get_db)):
    s = _import_or_404(db, session_id)
    if s.status != "review":
        raise HTTPException(status_code=409, detail=f"Import is {s.status}, not ready to accept")
    try:
        return resume_import.accept(db, s)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/{resume_id}", response_model=ResumeDetail)
def get_resume(resume_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, resume_id)


@router.get("/{resume_id}/pdf")
def download_pdf(resume_id: int, db: Session = Depends(get_db)):
    resume = _get_or_404(db, resume_id)
    if not resume.pdf_path:
        raise HTTPException(status_code=404, detail="No compiled PDF for this resume")
    return FileResponse(resume.pdf_path, media_type="application/pdf")


@router.put("/{resume_id}", status_code=201, response_model=ResumeOut)
def update_resume(resume_id: int, payload: ResumeUpdate, db: Session = Depends(get_db)):
    """Updates never mutate — they create a new version linked via parent_id."""
    base = _get_or_404(db, resume_id)
    try:
        return resume_bank.create_version(db, base, payload.tex_source, name=payload.name)
    except CompileError as e:
        raise HTTPException(status_code=422, detail=f"LaTeX compile failed: {e}") from e


@router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: int, db: Session = Depends(get_db)):
    """Deletes the resume's entire version family. Generated docs keep their
    own tex copy; just unlink the base reference."""
    resume = _get_or_404(db, resume_id)
    family = resume_bank.lineage(db, resume)
    ids = [r.id for r in family]
    pdfs = [r.pdf_path for r in family if r.pdf_path]
    db.query(GeneratedDoc).filter(GeneratedDoc.base_resume_id.in_(ids)).update(
        {"base_resume_id": None}, synchronize_session=False
    )
    for r in family:
        db.delete(r)
    db.commit()
    for p in pdfs:
        Path(p).unlink(missing_ok=True)


@router.post("/pdf", status_code=201, response_model=ResumeOut)
def upload_pdf_resume(
    file: UploadFile,
    name: str = Form(...),
    job_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """Store an external PDF in the bank (no LaTeX source; not tailorable)."""
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=422, detail="Upload a PDF file")
    resume = Resume(name=name, job_type=job_type, tex_source=None)
    db.add(resume)
    db.flush()
    dest_dir = settings.files_dir / "resumes"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"resume_{resume.id}.pdf"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        resume.page_count = pdf_page_count(dest)
    except Exception as e:
        db.rollback()
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="File is not a readable PDF") from e
    resume.pdf_path = str(dest)
    db.commit()
    return resume


@router.post("/bulk-edit")
def bulk_edit(payload: BulkEditRequest, db: Session = Depends(get_db)):
    """Apply a literal find/replace across the latest version of every LaTeX
    resume (optionally scoped to one job_type), creating new versions."""
    outcomes = resume_bank.bulk_find_replace(db, payload.find, payload.replace, payload.job_type)
    return {"results": [BulkEditResult(**vars(o)) for o in outcomes]}
