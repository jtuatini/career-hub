"""Brainstorm chat: POST a message, stream back SSE events from a headless
claude session (session/text/tool/done/error — see services/brainstorm.py)."""

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services import brainstorm as brainstorm_service

router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])


class MessageIn(BaseModel):
    message: str
    session_id: str | None = None


@router.post("/message")
async def send_message(payload: MessageIn) -> StreamingResponse:
    async def sse():
        async for event in brainstorm_service.stream_reply(payload.message, payload.session_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
