from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import Job, PrepSession
from app.services import prep as prep_service

router = APIRouter(prefix="/api/prep", tags=["prep"])


class SessionIn(BaseModel):
    job_id: int
    kind: Literal["interview", "oa"]


class TurnIn(BaseModel):
    answer: str


class SessionOut(BaseModel):
    id: int
    job_id: int
    kind: str
    status: str
    transcript: list[dict]
    report: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _session_or_404(db: Session, session_id: int) -> PrepSession:
    session = db.get(PrepSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Prep session not found")
    return session


@router.post("/sessions", status_code=201, response_model=SessionOut)
def create_session(payload: SessionIn, db: Session = Depends(get_db)):
    """interview: composes context + asks the first question synchronously
    (engine turn, seconds). oa: returns a running row that run_oa_research's
    daemon thread advances — poll GET /api/prep/sessions/{id}."""
    if db.get(Job, payload.job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if payload.kind == "interview":
        return prep_service.start_interview(db, payload.job_id)
    session = prep_service.start_oa(db, payload.job_id)
    prep_service.start_oa_research(session.id)
    return session


@router.post("/sessions/{session_id}/turn", response_model=SessionOut)
def turn(session_id: int, payload: TurnIn, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    try:
        return prep_service.answer_turn(db, session_id, payload.answer)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/sessions/{session_id}/finish", response_model=SessionOut)
def finish(session_id: int, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    try:
        return prep_service.finish_interview(db, session_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(job_id: int, db: Session = Depends(get_db)):
    return db.scalars(
        select(PrepSession)
        .where(PrepSession.job_id == job_id)
        .order_by(PrepSession.created_at.desc(), PrepSession.id.desc())
    ).all()


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)):
    return _session_or_404(db, session_id)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    db.delete(_session_or_404(db, session_id))
    db.commit()
