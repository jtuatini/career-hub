from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import CompanyResearch, GeneratedDoc, Job, JobStatus, utcnow
from app.services import jobparse as jobparse_service
from app.services import prep as prep_service
from app.services import research as research_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    company: str
    title: str
    url: str | None = None
    jd_text: str | None = None
    deadline: datetime | None = None
    notes: str | None = None


class JobPatch(BaseModel):
    status: JobStatus | None = None
    deadline: datetime | None = None
    notes: str | None = None
    jd_text: str | None = None
    url: str | None = None


class DocOut(BaseModel):
    id: int
    doc_type: str
    approved: bool
    vetted: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: int
    company: str
    title: str
    url: str | None
    status: str
    deadline: datetime | None
    applied_at: datetime | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobDetail(JobOut):
    jd_text: str | None
    docs: list[DocOut]


def _get_or_404(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("", status_code=201, response_model=JobOut)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    job = Job(**payload.model_dump())
    db.add(job)
    db.commit()
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(status: str | None = None, db: Session = Depends(get_db)):
    query = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
    if status:
        query = query.where(Job.status == status)
    return db.scalars(query).all()


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, job_id)


@router.patch("/{job_id}", response_model=JobOut)
def patch_job(job_id: int, payload: JobPatch, db: Session = Depends(get_db)):
    job = _get_or_404(db, job_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") == JobStatus.APPLIED and job.applied_at is None:
        job.applied_at = utcnow()
    for field, value in data.items():
        setattr(job, field, value)
    db.commit()
    return job


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = _get_or_404(db, job_id)
    db.query(GeneratedDoc).filter(GeneratedDoc.job_id == job.id).delete()
    db.delete(job)
    db.commit()


class ResearchOut(BaseModel):
    id: int
    job_id: int
    findings: str
    sources: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


@router.post("/{job_id}/research", response_model=ResearchOut)
def run_research(job_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Research the company via Claude's web-search tool; cached per job."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return research_service.research_company(db, job, force=force)


@router.get("/{job_id}/research", response_model=ResearchOut)
def get_research(job_id: int, db: Session = Depends(get_db)):
    cached = db.scalar(
        select(CompanyResearch)
        .where(CompanyResearch.job_id == job_id)
        .order_by(CompanyResearch.created_at.desc())
    )
    if cached is None:
        raise HTTPException(status_code=404, detail="No research yet for this job")
    return cached


@router.post("/{job_id}/prep")
def interview_prep(job_id: int, db: Session = Depends(get_db)):
    """Likely interview questions + best STAR stories from the brain. Not cached —
    regenerate as the brain grows."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return prep_service.build_prep(db, job)


class ParseRequest(BaseModel):
    text: str | None = None
    image_b64: str | None = None
    url: str | None = None


@router.post("/parse")
def parse_posting(payload: ParseRequest):
    """AI-parse a job posting from raw page text or a screenshot (exactly one)."""
    if bool(payload.text) == bool(payload.image_b64):
        raise HTTPException(status_code=422, detail="Provide exactly one of text or image_b64")
    if payload.text:
        return jobparse_service.parse_posting(payload.text, payload.url)
    import base64

    try:
        png = base64.b64decode(payload.image_b64)
    except Exception as e:
        raise HTTPException(status_code=422, detail="image_b64 is not valid base64") from e
    return jobparse_service.parse_posting_image(png)
