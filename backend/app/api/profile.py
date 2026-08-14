import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.db.models import ProfileField
from app.services import auth
from app.services import autofill as autofill_service
from app.services import github_sync

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileUpdate(BaseModel):
    values: dict[str, str]


@router.get("")
def get_profile(db: Session = Depends(get_db)) -> dict[str, str]:
    return autofill_service.load_profile(db)


@router.put("")
def put_profile(payload: ProfileUpdate, db: Session = Depends(get_db)) -> dict[str, str]:
    """Bulk upsert; empty values delete the key."""
    existing = {f.key: f for f in db.query(ProfileField).all()}
    for key, value in payload.values.items():
        key = key.strip()
        if not key:
            continue
        if not value.strip():
            if key in existing:
                db.delete(existing[key])
            continue
        if key in existing:
            existing[key].value = value
        else:
            db.add(ProfileField(key=key, value=value))
    db.commit()
    return autofill_service.load_profile(db)


@router.post("/extension-token")
def extension_token() -> dict[str, str]:
    """The shared API token, for pasting into the extension popup once.
    Only reachable by already-authenticated callers (the web app). POST-only:
    a deliberate reveal action, never a routine read that could end up in
    caches, logs, or a casual same-origin fetch."""
    return {"token": auth.get_token()}


class MapRequest(BaseModel):
    fields: list[dict]
    use_ai: bool = False


@router.post("/map")
def map_fields(payload: MapRequest, db: Session = Depends(get_db)):
    """Map form-field descriptors to profile values for extension autofill."""
    if not payload.fields:
        raise HTTPException(status_code=422, detail="No fields provided")
    return {"mappings": autofill_service.map_fields(db, payload.fields, payload.use_ai)}


@router.post("/github/sync")
def github_repos_sync(db: Session = Depends(get_db)):
    """User-triggered: pull public repos into the brain as project hubs."""
    try:
        return github_sync.sync(db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"GitHub unreachable: {e}") from e
