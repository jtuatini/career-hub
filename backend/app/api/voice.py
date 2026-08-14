from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import StyleProfile, VoiceSample
from app.services import ingest as ingest_service
from app.services import voice as voice_service
from app.services.claude import ClaudeError

router = APIRouter(prefix="/api/voice", tags=["voice"])

VALID_KINDS = {"formal", "informal"}
MIN_TEXT = 40


class SampleCreate(BaseModel):
    title: str
    kind: str
    text: str


class SampleOut(BaseModel):
    id: int
    title: str
    kind: str
    source: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProfileOut(BaseModel):
    content: str | None
    learned_rules: list[dict]
    updated_at: datetime | None
    sample_count: int


class ProfileUpdate(BaseModel):
    content: str | None = None
    learned_rules: list[dict] | None = None


def _validate_sample(kind: str, text: str) -> None:
    if kind not in VALID_KINDS:
        raise HTTPException(status_code=422, detail="kind must be formal or informal")
    if len(text.strip()) < MIN_TEXT:
        raise HTTPException(status_code=422, detail="Sample is too short to be useful")


@router.post("/samples", status_code=201, response_model=SampleOut)
def add_sample(payload: SampleCreate, db: Session = Depends(get_db)):
    _validate_sample(payload.kind, payload.text)
    sample = VoiceSample(
        title=payload.title, kind=payload.kind, source="paste", text=payload.text.strip()
    )
    db.add(sample)
    db.commit()
    return sample


@router.post("/samples/upload", status_code=201, response_model=SampleOut)
def upload_sample(
    file: UploadFile,
    title: str = Form(...),
    kind: str = Form(...),
    db: Session = Depends(get_db),
):
    filename = file.filename or "upload"
    text = ingest_service.extract_text(filename, file.file.read()).strip()
    _validate_sample(kind, text)
    sample = VoiceSample(title=title, kind=kind, source=filename, text=text)
    db.add(sample)
    db.commit()
    return sample


@router.get("/samples", response_model=list[SampleOut])
def list_samples(db: Session = Depends(get_db)):
    return db.scalars(select(VoiceSample).order_by(VoiceSample.created_at.desc())).all()


@router.delete("/samples/{sample_id}", status_code=204)
def delete_sample(sample_id: int, db: Session = Depends(get_db)):
    sample = db.get(VoiceSample, sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    db.delete(sample)
    db.commit()


def _profile_out(db: Session) -> ProfileOut:
    profile = voice_service.get_profile(db)
    count = len(db.scalars(select(VoiceSample.id)).all())
    return ProfileOut(
        content=profile.content if profile else None,
        learned_rules=profile.learned_rules if profile else [],
        updated_at=profile.updated_at if profile else None,
        sample_count=count,
    )


@router.get("/profile", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db)):
    return _profile_out(db)


@router.put("/profile", response_model=ProfileOut)
def update_profile(payload: ProfileUpdate, db: Session = Depends(get_db)):
    profile = voice_service.get_profile(db)
    if profile is None:
        profile = StyleProfile(content=payload.content or "", learned_rules=payload.learned_rules or [])
        db.add(profile)
    else:
        if payload.content is not None:
            profile.content = payload.content
        if payload.learned_rules is not None:
            profile.learned_rules = payload.learned_rules
    db.commit()
    return _profile_out(db)


@router.post("/profile/rebuild", response_model=ProfileOut)
def rebuild(db: Session = Depends(get_db)):
    try:
        voice_service.build_profile(db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (ClaudeError, RuntimeError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return _profile_out(db)
