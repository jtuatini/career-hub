from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api import (
    apply,
    ats,
    brainstorm,
    docs,
    engine,
    generate,
    insights,
    jobs,
    memory,
    network,
    prep,
    profile,
    qa,
    resumes,
    terminal,
    voice,
)
from app.services.claude import ClaudeError

from app.services import auth

app = FastAPI(title="Application Copilot", docs_url="/api/redoc", openapi_url="/api/openapi.json")

# Paths callable without the shared token: health checks and API discovery.
TOKEN_EXEMPT = {"/api/health", "/api/redoc", "/api/openapi.json"}


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    """Shared-token gate for REST. WebSockets are guarded by the Origin check in
    api/terminal.py instead. Registered before CORSMiddleware so CORS stays
    outermost and answers preflights (which never carry custom headers)."""
    path = request.url.path
    if path.startswith("/api") and path not in TOKEN_EXEMPT:
        if request.headers.get("x-copilot-token") != auth.get_token():
            # New-tab PDF opens can't send headers: accept a short-lived signed ticket.
            params = request.query_params
            if (
                request.method == "GET"
                and path.endswith("/pdf")
                and auth.verify_ticket(path, params.get("exp"), params.get("sig"))
            ):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Missing or invalid X-Copilot-Token. The web app sends it "
                    "automatically (restart `npm run dev` if you just started the backend "
                    "for the first time); for the extension, paste the token from the "
                    "app's Profile tab."
                },
            )
    return await call_next(request)
app.include_router(apply.router)
app.include_router(ats.router)
app.include_router(prep.router)
app.include_router(resumes.router)
app.include_router(jobs.router)
app.include_router(generate.router)
app.include_router(docs.router)
app.include_router(terminal.router)
app.include_router(memory.router)
app.include_router(qa.router)
app.include_router(brainstorm.router)
app.include_router(profile.router)
app.include_router(insights.router)
app.include_router(engine.router)
app.include_router(voice.router)
app.include_router(network.router)

# Vite dev server + the Chrome extension popup (chrome-extension:// origin).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ClaudeError)
def claude_unavailable(_request: Request, exc: ClaudeError) -> JSONResponse:
    """Claude API problems (missing/invalid key, refusal) are service conditions,
    not crashes — surface the actionable message."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/testbed")
def testbed() -> FileResponse:
    """Local dummy application: a two-page wizard with every field shape the
    extension knows (plus traps), for exercising the fill pipeline end-to-end.
    Static HTML, no form action, never sends anything anywhere. Outside /api on
    purpose: the extension's content script visits it like any job page."""
    return FileResponse(Path(__file__).parent / "static" / "testbed.html", media_type="text/html")


@app.get("/testbed/greenhouse")
def testbed_greenhouse() -> FileResponse:
    """Greenhouse-clone trial application: every dropdown is a react-select
    combobox (typed text never commits; blur clears it), file inputs hide
    behind Attach buttons, EEOC section uses the real platform wordings."""
    return FileResponse(Path(__file__).parent / "static" / "testbed_greenhouse.html", media_type="text/html")


@app.get("/testbed/workday")
def testbed_workday() -> FileResponse:
    """Workday-clone trial application: same-URL SPA wizard (posting → account
    → 4 steps → review) with listbox buttons, promptOption search widgets,
    MM/DD/YYYY date segments, loading spinners, and validation-blocked Next."""
    return FileResponse(Path(__file__).parent / "static" / "testbed_workday.html", media_type="text/html")
