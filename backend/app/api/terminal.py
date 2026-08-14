"""WebSocket bridge between xterm.js and the embedded Claude Code PTY session.

Protocol: binary frames carry terminal bytes both directions; text frames carry JSON
control messages — client sends {"type": "resize", "cols": N, "rows": N}, server sends
{"type": "exit"} (session ended), {"type": "replaced"} (another tab attached), or
{"type": "error", "message": ...}.
"""

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services import terminal as terminal_service

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

# Browser pages other than the local frontend must not reach the PTY: a malicious
# site connecting here could drive the Claude session, including approving its own
# tool calls. Membership is exact and the header is REQUIRED — browsers always
# send Origin on WebSocket handshakes, so an absent header is a non-browser
# client, and those must pass the same gate rather than fail open on the one
# endpoint that amounts to command execution.
ALLOWED_WS_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}

_active_ws: WebSocket | None = None


@router.post("/restart")
def restart_session() -> dict[str, str]:
    terminal_service.restart()
    return {"status": "restarted"}


async def _pump_session_to_ws(ws: WebSocket, queue: asyncio.Queue[bytes]) -> None:
    while True:
        data = await queue.get()
        if not data:
            await ws.send_text(json.dumps({"type": "exit"}))
            return
        await ws.send_bytes(data)


async def _pump_ws_to_session(ws: WebSocket, session: terminal_service.TerminalSession) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            session.write(message["bytes"])
        elif message.get("text"):
            control = json.loads(message["text"])
            if control.get("type") == "resize":
                session.resize(int(control["cols"]), int(control["rows"]))


@router.websocket("/ws")
async def terminal_ws(ws: WebSocket) -> None:
    global _active_ws
    origin = ws.headers.get("origin")
    if origin not in ALLOWED_WS_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        session = terminal_service.get_or_create()
    except FileNotFoundError as e:
        await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        await ws.close()
        return

    # One attached client: a second tab takes the session over from the first.
    if _active_ws is not None:
        with contextlib.suppress(Exception):
            await _active_ws.send_text(json.dumps({"type": "replaced"}))
            await _active_ws.close(code=4000)
    _active_ws = ws

    queue: asyncio.Queue[bytes] = asyncio.Queue()
    replay = session.attach(queue)
    if replay:
        await ws.send_bytes(replay)
        session.nudge_repaint()

    tasks = [
        asyncio.create_task(_pump_session_to_ws(ws, queue)),
        asyncio.create_task(_pump_ws_to_session(ws, session)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                task.result()
    finally:
        session.detach(queue)
        if _active_ws is ws:
            _active_ws = None
        with contextlib.suppress(RuntimeError):
            await ws.close()
