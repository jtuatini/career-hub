from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import ApplySession, GeneratedDoc
from app.services import apply as apply_service
from app.services import fillplan
from app.services import memory as memory_service
from app.services import voice as voice_service

router = APIRouter(prefix="/api/apply", tags=["apply"])


class ApplyOptions(BaseModel):
    tailor_resume: bool = True
    cover_letter: bool = True
    answer_questions: bool = True


class SessionCreate(BaseModel):
    url: str
    page_text: str
    fields: list[dict] = []
    buttons: list[dict] = []
    mode: Literal["full", "fill_only", "tailor_only"] = "full"
    options: ApplyOptions = ApplyOptions()


class PageIn(BaseModel):
    url: str = ""
    fields: list[dict] = []
    buttons: list[dict] = []


class ReportIn(BaseModel):
    results: list[dict] = []
    edits: list[dict] = []
    done: bool = False


class RetryIn(BaseModel):
    resume_id: int | None = None
    options: ApplyOptions | None = None


class SessionOut(BaseModel):
    id: int
    status: str
    stage: str
    progress: float
    error: str | None
    job_id: int | None
    resume_doc_id: int | None
    cover_doc_id: int | None

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, session_id: int) -> ApplySession:
    s = db.get(ApplySession, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Apply session not found")
    return s


@router.post("/sessions", status_code=201)
def create(payload: SessionCreate, db: Session = Depends(get_db)):
    s = apply_service.create_session(
        db, payload.url, payload.page_text, payload.fields, payload.buttons, payload.mode,
        payload.options.model_dump(),
    )
    apply_service.start_pipeline(s.id)
    return {"id": s.id}


@router.get("/sessions/{session_id}", response_model=SessionOut)
def status(session_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, session_id)


@router.post("/sessions/{session_id}/page")
def page(session_id: int, payload: PageIn, db: Session = Depends(get_db)):
    s = _get_or_404(db, session_id)
    actions = fillplan.build_plan(db, s, payload.fields, payload.buttons)
    db.commit()  # persists qa_drafts written into s.state
    return {"actions": actions}


@router.post("/sessions/{session_id}/report")
def report(session_id: int, payload: ReportIn, db: Session = Depends(get_db)):
    s = _get_or_404(db, session_id)
    for edit in payload.edits:
        question = (edit.get("label") or "").strip()
        final = (edit.get("final") or "").strip()
        draft = (edit.get("draft") or "").strip()
        if not question or not final:
            continue
        memory_service.save_qa(db, question, final, ["apply"], s.job_id)
        if draft and draft != final:
            voice_service.learn_from_edit(db, draft, final, "supplemental answer")
    s.state = {**s.state, "results": list(s.state.get("results", [])) + payload.results}
    if payload.done:
        s.status = "done"
    db.commit()
    return {"ok": True}


@router.post("/sessions/{session_id}/retry", status_code=202)
def retry(session_id: int, payload: RetryIn, db: Session = Depends(get_db)):
    s = _get_or_404(db, session_id)
    if s.status == "running":
        raise HTTPException(status_code=409, detail="Session is still running — stop it first or wait")
    s.status = "running"
    s.error = None
    # A retry is a deliberate refill — forget the same-page replan guard so the
    # current page plans fresh instead of being treated as a stalled duplicate.
    s.state = {**s.state, "last_page_sig": None, "stall_notified": None, "nav_clicked_sig": None}
    if payload.options is not None and s.state.get("mode") != "tailor_only":
        # Retries re-run only missing stages; refreshed switches let a user
        # who just unchecked a toggle retry without resurrecting that stage.
        # tailor_only is a hard preset (services/apply.py create_session) — a
        # retry must never let passed-in options resurrect cover_letter or
        # answer_questions on that mode, so options are silently ignored here.
        s.state = {**s.state, "options": payload.options.model_dump()}
    if payload.resume_id is not None:
        s.state = {**s.state, "resume_override": payload.resume_id, "base_resume_id": None}
        # Delete old GeneratedDoc if present and unlink PDF
        if s.resume_doc_id is not None:
            old_doc = db.get(GeneratedDoc, s.resume_doc_id)
            if old_doc is not None:
                if old_doc.pdf_path:
                    Path(old_doc.pdf_path).unlink(missing_ok=True)
                db.delete(old_doc)
                db.flush()
        s.resume_doc_id = None
    db.commit()
    apply_service.start_pipeline(s.id)
    return {"id": s.id}


@router.post("/sessions/{session_id}/stop")
def stop(session_id: int, db: Session = Depends(get_db)):
    s = _get_or_404(db, session_id)
    s.status = "stopped"
    db.commit()
    return {"status": "stopped"}
