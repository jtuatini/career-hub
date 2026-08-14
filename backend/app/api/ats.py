from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import AtsScan, GeneratedDoc, Resume
from app.services import ats_scan as ats_service
from app.services import engine as engine_service

router = APIRouter(prefix="/api/ats", tags=["ats"])


class ScanIn(BaseModel):
    doc_id: int | None = None
    resume_id: int | None = None
    kind: Literal["keyword", "jd_match", "deep", "hiring_agent"]


class ScanOut(BaseModel):
    id: int
    doc_id: int | None
    resume_id: int | None
    kind: str
    status: str
    report: dict | None
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _validate_target(db: Session, doc_id: int | None, resume_id: int | None) -> None:
    if (doc_id is None) == (resume_id is None):
        raise HTTPException(status_code=422, detail="Provide exactly one of doc_id or resume_id")
    if doc_id is not None and db.get(GeneratedDoc, doc_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if resume_id is not None and db.get(Resume, resume_id) is None:
        raise HTTPException(status_code=404, detail="Resume not found")


def _preflight(kind: str) -> None:
    """Fail fast with a fixable message instead of starting a doomed scan."""
    if kind in ("jd_match", "deep"):
        st = engine_service.status()
        if not (st["subscription_available"] or st["api_key_configured"]):
            raise HTTPException(
                status_code=409,
                detail="No AI engine available — log in a CLI engine or add an API key "
                "(engine chip in the header), then re-run the scan.",
            )
    elif kind == "hiring_agent":
        if not ats_service.hiring_agent_available():
            raise HTTPException(
                status_code=409,
                detail="Hiring-agent repo not configured — set ATS_REPO_PATH in backend/.env.",
            )
        if not ats_service.ollama_running():
            raise HTTPException(
                status_code=409,
                detail="Ollama isn't running — the hiring-agent scan is fully local and needs "
                "it. Start it with `ollama serve`, then re-run.",
            )


@router.post("/scan", status_code=201, response_model=ScanOut)
def create_scan(payload: ScanIn, db: Session = Depends(get_db)):
    """keyword completes inline (local, instant); AI kinds return a running
    row that run_scan's daemon thread advances — poll GET /api/ats/scan/{id}."""
    _validate_target(db, payload.doc_id, payload.resume_id)
    _preflight(payload.kind)
    scan = AtsScan(doc_id=payload.doc_id, resume_id=payload.resume_id, kind=payload.kind)
    db.add(scan)
    db.commit()
    if payload.kind == "keyword":
        ats_service.run_keyword(db, scan)
    else:
        ats_service.start_scan(scan.id)
    return scan


@router.get("/scan/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    scan = db.get(AtsScan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return scan


@router.post("/scan/{scan_id}/cancel", response_model=ScanOut)
def cancel_scan(scan_id: int, db: Session = Depends(get_db)):
    """Only "running" rows can be cancelled — keyword scans complete inline
    so they're never running by the time a client could call this, and
    done/error/cancelled rows are already terminal. See run_scan's docstring
    for how the background thread avoids resurrecting the row afterward."""
    scan = db.get(AtsScan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    if scan.status != "running":
        raise HTTPException(status_code=409, detail="Scan is not running")
    scan.status = "cancelled"
    db.commit()
    return scan


class ScanListOut(BaseModel):
    scans: list[ScanOut]
    capabilities: dict[str, bool]


@router.get("/scans", response_model=ScanListOut)
def list_scans(
    doc_id: int | None = None,
    resume_id: int | None = None,
    db: Session = Depends(get_db),
):
    _validate_target(db, doc_id, resume_id)
    query = select(AtsScan).order_by(AtsScan.created_at.desc(), AtsScan.id.desc()).limit(50)
    if doc_id is not None:
        query = query.where(AtsScan.doc_id == doc_id)
    else:
        query = query.where(AtsScan.resume_id == resume_id)
    return ScanListOut(
        scans=db.scalars(query).all(),
        capabilities=ats_service.capabilities(db, doc_id=doc_id, resume_id=resume_id),
    )
