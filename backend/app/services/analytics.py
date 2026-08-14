"""Tracker analytics: funnel, per-resume response rates, reminders. All local."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocType, GeneratedDoc, Job, JobStatus, Person, Resume, utcnow


def _aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; ours are always UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

RESPONSE_STATUSES = {JobStatus.OA, JobStatus.INTERVIEW, JobStatus.OFFER, JobStatus.REJECTED}
INTERVIEW_STATUSES = {JobStatus.INTERVIEW, JobStatus.OFFER}
STALE_AFTER_DAYS = 14
DEADLINE_WINDOW_DAYS = 7


def funnel(db: Session) -> dict[str, int]:
    jobs = db.scalars(select(Job)).all()
    counts = {status.value: 0 for status in JobStatus}
    for job in jobs:
        counts[job.status] += 1
    counts["total"] = len(jobs)
    return counts


def by_resume(db: Session) -> list[dict]:
    """Response stats per base resume actually used in applications."""
    docs = db.scalars(
        select(GeneratedDoc).where(
            GeneratedDoc.doc_type == DocType.RESUME,
            GeneratedDoc.base_resume_id.is_not(None),
        )
    ).all()
    jobs_by_resume: dict[int, set[int]] = {}
    for doc in docs:
        jobs_by_resume.setdefault(doc.base_resume_id, set()).add(doc.job_id)

    rows = []
    for resume_id, job_ids in jobs_by_resume.items():
        resume = db.get(Resume, resume_id)
        jobs = [db.get(Job, j) for j in job_ids]
        jobs = [j for j in jobs if j is not None]
        applied = [j for j in jobs if j.status != JobStatus.SAVED]
        responses = [j for j in applied if j.status in RESPONSE_STATUSES]
        rows.append(
            {
                "resume_id": resume_id,
                "name": resume.name if resume else f"deleted #{resume_id}",
                "job_type": resume.job_type if resume else "?",
                "jobs_tailored": len(jobs),
                "applications": len(applied),
                "responses": len(responses),
                "interviews": len([j for j in applied if j.status in INTERVIEW_STATUSES]),
                "offers": len([j for j in applied if j.status == JobStatus.OFFER]),
                "response_rate": round(len(responses) / len(applied), 3) if applied else None,
            }
        )
    rows.sort(key=lambda r: (-(r["response_rate"] or 0), -r["applications"]))
    return rows


def reminders(db: Session) -> dict[str, list[dict]]:
    now = utcnow()
    jobs = db.scalars(select(Job)).all()

    def brief(job: Job) -> dict:
        return {
            "id": job.id,
            "company": job.company,
            "title": job.title,
            "status": job.status,
            "deadline": job.deadline.isoformat() if job.deadline else None,
            "applied_at": job.applied_at.isoformat() if job.applied_at else None,
        }

    deadlines = [
        brief(j)
        for j in jobs
        if j.status == JobStatus.SAVED
        and j.deadline is not None
        and _aware(j.deadline) <= now + timedelta(days=DEADLINE_WINDOW_DAYS)
    ]
    deadlines.sort(key=lambda j: j["deadline"])

    stale = [
        brief(j)
        for j in jobs
        if j.status == JobStatus.APPLIED
        and j.applied_at is not None
        and _aware(j.applied_at) <= now - timedelta(days=STALE_AFTER_DAYS)
    ]
    stale.sort(key=lambda j: j["applied_at"])
    return {"deadlines": deadlines, "stale": stale}


CLOSED_STATUSES = {JobStatus.REJECTED, JobStatus.WITHDRAWN}
PREP_STATUSES = {JobStatus.OA, JobStatus.INTERVIEW}


def action_queue(db: Session) -> dict:
    """What needs doing next, for the dashboard. Newest first; the UI caps
    what it renders and reports "+N more" from the full count here."""
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
    docs = db.scalars(select(GeneratedDoc).order_by(GeneratedDoc.created_at.desc())).all()
    tailored_jobs = {d.job_id for d in docs if d.doc_type == DocType.RESUME}

    def brief(job: Job) -> dict:
        return {"job_id": job.id, "company": job.company, "title": job.title, "status": job.status}

    return {
        "needs_resume": [
            brief(j) for j in jobs
            if j.status not in CLOSED_STATUSES and j.id not in tailored_jobs
        ],
        "prep_ready": [brief(j) for j in jobs if j.status in PREP_STATUSES],
        "drafts": [
            {
                "doc_id": d.id,
                "doc_type": d.doc_type,
                "company": d.job.company if d.job else "?",
                "title": d.job.title if d.job else "?",
            }
            for d in docs if not d.approved
        ],
    }


def counts(db: Session) -> dict:
    docs = db.scalars(select(GeneratedDoc)).all()
    return {
        "tailored": sum(1 for d in docs if d.doc_type == DocType.RESUME),
        "letters": sum(1 for d in docs if d.doc_type == DocType.COVER_LETTER),
        "bank_resumes": len(db.scalars(select(Resume)).all()),
        "jobs": len(db.scalars(select(Job)).all()),
        "network_people": len(db.scalars(select(Person)).all()),
    }
