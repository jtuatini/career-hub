from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import engine as engine_service
from app.services import engine_prefs

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("/status")
def engine_status() -> dict:
    """Full provider-registry payload (Phase 4): the legacy 4 keys plus
    ai_provider/providers/last_provider — Task 14's frontend picker reads
    ai_provider and providers straight from this endpoint."""
    return engine_service.status()


class ProviderIn(BaseModel):
    provider: Literal["claude", "codex", "gemini"]


@router.put("/provider")
def set_provider(payload: ProviderIn) -> dict:
    engine_prefs.set_provider(payload.provider)
    return engine_service.status()


class ModelIn(BaseModel):
    provider: Literal["claude", "codex", "gemini"]
    model: str = ""


@router.put("/model")
def set_model(payload: ModelIn) -> dict:
    """CLI model override for one provider; empty string reverts to the
    .env default. Applies to subscription-CLI runs only — the metered-API
    fallback keeps settings.claude_model (CLI aliases aren't API model IDs)."""
    engine_prefs.set_model(payload.provider, payload.model)
    return engine_service.status()
