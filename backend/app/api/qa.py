from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import QABankEntry
from app.services import answers as answers_service
from app.services import memory as memory_service
from app.services import voice as voice_service

router = APIRouter(prefix="/api/qa", tags=["qa"])


class QACreate(BaseModel):
    question: str
    answer: str
    tags: list[str] = []
    job_id: int | None = None
    draft: str | None = None


class QAOut(BaseModel):
    id: int
    question: str
    answer: str
    tags: list[str]
    job_id: int | None
    times_used: int
    created_at: datetime

    model_config = {"from_attributes": True}


class QASearchRequest(BaseModel):
    query: str
    k: int = 5


class QASearchHit(BaseModel):
    score: float
    qa: QAOut


@router.post("", status_code=201, response_model=QAOut)
def save_answer(payload: QACreate, db: Session = Depends(get_db)):
    qa = memory_service.save_qa(db, payload.question, payload.answer, payload.tags, payload.job_id)
    if payload.draft and payload.draft != payload.answer:
        voice_service.learn_from_edit(db, payload.draft, payload.answer, "supplemental answer")
    return qa


@router.get("", response_model=list[QAOut])
def list_answers(db: Session = Depends(get_db)):
    return db.scalars(
        select(QABankEntry).order_by(QABankEntry.created_at.desc(), QABankEntry.id.desc())
    ).all()


@router.post("/search", response_model=list[QASearchHit])
def search(payload: QASearchRequest, db: Session = Depends(get_db)):
    hits = memory_service.search_qa(db, payload.query, payload.k)
    return [QASearchHit(score=score, qa=QAOut.model_validate(qa)) for qa, score in hits]


class DraftRequest(BaseModel):
    question: str
    job_id: int | None = None


@router.post("/draft")
def draft(payload: DraftRequest, db: Session = Depends(get_db)):
    """Draft an answer in the user's voice from brain retrieval + past answers.
    The draft is NOT saved — edit, approve, then POST /api/qa to bank it."""
    try:
        return answers_service.draft_answer(db, payload.question, payload.job_id)
    except RuntimeError as e:  # missing API key, refusal
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post("/{qa_id}/mark-used", response_model=QAOut)
def mark_used(qa_id: int, db: Session = Depends(get_db)):
    qa = db.get(QABankEntry, qa_id)
    if qa is None:
        raise HTTPException(status_code=404, detail="Q&A entry not found")
    qa.times_used += 1
    db.commit()
    return qa
