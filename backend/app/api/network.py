import threading
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.base import SessionLocal, get_db
from app.db.models import NetworkTarget, Person
from app.services import network as network_service
from app.services.claude import ClaudeError

router = APIRouter(prefix="/api/network", tags=["network"])

PersonStatus = Literal["found", "shortlisted", "contacted", "replied", "met", "archived"]
PersonType = Literal["alumni", "engineer", "recruiter", "manager", "other"]


# --- background runners -----------------------------------------------------

_status = {"running": False, "done": 0, "total": 0, "last_error": None}


def _discover_worker(target_ids: list[int], force: bool) -> None:
    """The real body behind _run_discover_async's thread — extracted so tests
    can call it directly (synchronously) instead of racing a background thread.

    Callers (normally _run_discover_async) are responsible for setting
    _status's running/done/total/last_error BEFORE this runs (Finding 4) —
    doing it here, at the top of the thread body, left a window where a
    request that polls /discover/status immediately after POST /discover
    (or a second POST checking the double-start guard) could still observe
    the pre-run state."""
    db = SessionLocal()
    try:
        for tid in target_ids:
            try:
                t = db.get(NetworkTarget, tid)
                if t is None:
                    continue
                for person in network_service.discover(db, t, force=force):
                    network_service.enrich(db, person)
            except ClaudeError as e:
                _status["last_error"] = str(e)
                # A failed discover/enrich call can leave the session needing a
                # rollback (e.g. a commit raised mid-discover() on a locked
                # SQLite). Roll back here so the NEXT iteration's db.get() runs
                # against a clean session instead of raising
                # PendingRollbackError and escaping the loop — one bad target
                # must never drop the remaining ones.
                db.rollback()
            except Exception as e:  # never kill the thread
                _status["last_error"] = str(e)
                db.rollback()
            finally:
                _status["done"] += 1
    finally:
        db.close()
        _status["running"] = False


def _run_discover_async(target_ids: list[int], force: bool) -> None:
    # Set status synchronously, in the caller's thread, BEFORE the worker
    # thread starts (Finding 4) — see _discover_worker's docstring for why.
    _status.update(running=True, done=0, total=len(target_ids), last_error=None)
    threading.Thread(target=_discover_worker, args=(target_ids, force), daemon=True).start()


def _run_enrich_async(person_id: int) -> None:
    def worker():
        db = SessionLocal()
        try:
            p = db.get(Person, person_id)
            if p is not None:
                network_service.enrich(db, p)
        finally:
            db.close()

    threading.Thread(target=worker, daemon=True).start()


# --- targets -----------------------------------------------------------------


class TargetCreate(BaseModel):
    company: str
    role_type: str | None = None


class TargetPatch(BaseModel):
    active: bool | None = None
    role_type: str | None = None


class TargetOut(BaseModel):
    id: int
    company: str
    role_type: str | None
    source: str
    active: bool
    discovered_at: datetime | None

    model_config = {"from_attributes": True}


def _get_target_or_404(db: Session, target_id: int) -> NetworkTarget:
    target = db.get(NetworkTarget, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return target


@router.get("/targets", response_model=list[TargetOut])
def list_targets(db: Session = Depends(get_db)):
    network_service.sync_targets(db)
    return db.scalars(select(NetworkTarget).order_by(NetworkTarget.id)).all()


@router.post("/targets", status_code=201, response_model=TargetOut)
def create_target(payload: TargetCreate, response: Response, db: Session = Depends(get_db)):
    # Idempotent create (Finding 3): re-adding a target that's already there
    # (e.g. double-clicking "add", or the UI re-submitting the same chip)
    # must not mint a duplicate — a duplicate manual target doubles discovery
    # spend for that company. Uniqueness is enforced here at the app level
    # (case-insensitive company + exact role_type, active manual targets
    # only) rather than a DB constraint, since NetworkTarget has none.
    existing = db.scalar(
        select(NetworkTarget).where(
            NetworkTarget.source == "manual",
            NetworkTarget.active.is_(True),
            func.lower(NetworkTarget.company) == payload.company.lower(),
            NetworkTarget.role_type == payload.role_type,
        )
    )
    if existing is not None:
        response.status_code = 200
        return existing
    target = NetworkTarget(**payload.model_dump(), source="manual")
    db.add(target)
    db.commit()
    return target


@router.patch("/targets/{target_id}", response_model=TargetOut)
def patch_target(target_id: int, payload: TargetPatch, db: Session = Depends(get_db)):
    target = _get_target_or_404(db, target_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    db.commit()
    return target


@router.delete("/targets/{target_id}", status_code=204)
def delete_target(target_id: int, db: Session = Depends(get_db)):
    target = _get_target_or_404(db, target_id)
    if target.source != "manual":
        raise HTTPException(status_code=409, detail="Derived targets can't be deleted directly")
    db.delete(target)
    db.commit()


# --- discover ------------------------------------------------------------


class DiscoverRequest(BaseModel):
    target_id: int | None = None
    force: bool = False


@router.post("/discover", status_code=202)
def start_discover(payload: DiscoverRequest, db: Session = Depends(get_db)):
    if _status["running"]:
        # Idempotent no-op: a double-click (or a duplicate request) while a
        # run is already in flight would otherwise spawn a second worker over
        # the same targets, garbling _status and doubling API spend. _status
        # now flips True synchronously in _run_discover_async, before the
        # worker thread even starts (Finding 4), so this guard is reliable
        # against a second request issued after this one returns. Two truly
        # concurrent requests could still both pass this check before either
        # sets the flag — an unguarded check-then-set — but this is a
        # single-user local app; accepted rather than adding a lock for a
        # window this narrow.
        return {"started": 0}
    if payload.target_id is not None:
        _get_target_or_404(db, payload.target_id)
        ids = [payload.target_id]
    else:
        ids = list(db.scalars(select(NetworkTarget.id).where(NetworkTarget.active.is_(True))).all())
    _run_discover_async(ids, payload.force)
    return {"started": len(ids)}


@router.get("/discover/status")
def discover_status():
    return dict(_status)


# --- people ----------------------------------------------------------------


class PersonCreate(BaseModel):
    name: str
    company: str
    headline: str | None = None
    profile_url: str | None = None
    location: str | None = None
    # The API never legitimately mints source="web_search" people — that
    # value is only ever set by the discovery service, which requires an
    # evidence URL before creating a person (the evidence invariant). Callers
    # of this endpoint are limited to the two ways a person can be created by
    # hand (Finding 1).
    source: Literal["linkedin_capture", "manual"] = "manual"


class PersonPatch(BaseModel):
    status: PersonStatus | None = None
    notes: str | None = None
    connection_note: str | None = None
    followup: str | None = None


class PersonOut(BaseModel):
    id: int
    name: str
    headline: str | None
    company: str
    location: str | None
    person_type: str
    profile_url: str | None
    evidence_urls: list[str]
    source: str
    match_signals: list[dict]
    summary: str | None
    connection_note: str | None
    followup: str | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _get_person_or_404(db: Session, person_id: int) -> Person:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@router.get("/people", response_model=list[PersonOut])
def list_people(
    status: PersonStatus | None = None,
    company: str | None = None,
    person_type: PersonType | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(Person).order_by(Person.id)
    if status:
        query = query.where(Person.status == status)
    if company:
        query = query.where(Person.company == company)
    if person_type:
        query = query.where(Person.person_type == person_type)
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                Person.name.ilike(like),
                Person.headline.ilike(like),
                Person.company.ilike(like),
            )
        )
    return db.scalars(query).all()


@router.post("/people", status_code=201, response_model=PersonOut)
def create_person(payload: PersonCreate, db: Session = Depends(get_db)):
    person = Person(**payload.model_dump())
    db.add(person)
    db.commit()
    _run_enrich_async(person.id)
    return person


@router.post("/people/{person_id}/enrich", status_code=202)
def enrich_person(person_id: int, db: Session = Depends(get_db)):
    _get_person_or_404(db, person_id)
    _run_enrich_async(person_id)
    return {"started": True}


@router.patch("/people/{person_id}", response_model=PersonOut)
def patch_person(person_id: int, payload: PersonPatch, db: Session = Depends(get_db)):
    person = _get_person_or_404(db, person_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(person, field, value)
    db.commit()
    return person


@router.delete("/people/{person_id}", status_code=204)
def delete_person(person_id: int, db: Session = Depends(get_db)):
    person = _get_person_or_404(db, person_id)
    db.delete(person)
    db.commit()
