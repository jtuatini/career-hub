import hashlib

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.base import Base, get_db
from app.main import app
from app.services import embeddings


@pytest.fixture(autouse=True)
def _no_real_ai_in_tests(monkeypatch):
    """Force the API engine path so no test ever spawns the real claude CLI
    (subscription tokens!). Engine tests opt back in against a stub binary."""
    monkeypatch.setattr(settings, "ai_engine", "api")


@pytest.fixture(autouse=True)
def _no_real_autolink_in_tests(monkeypatch):
    """Auto-linking is best-effort; default it to a stub failure so tests that
    create entries never reach a real engine. graphlink tests re-patch it."""
    from app.services import graphlink
    from app.services.claude import ClaudeError

    def _disabled(*args, **kwargs):
        raise ClaudeError("auto-link disabled in tests")

    monkeypatch.setattr(graphlink, "generate_json", _disabled)


@pytest.fixture(autouse=True)
def _no_real_voice_ai_in_tests(monkeypatch):
    """Voice + cover-letter engine calls default to a stub failure so no test
    reaches a real engine. Voice/CL tests re-patch per-test."""
    from app.services import coverletter, voice
    from app.services.claude import ClaudeError

    def _disabled(*args, **kwargs):
        raise ClaudeError("voice AI disabled in tests")

    monkeypatch.setattr(voice, "generate_json", _disabled)
    monkeypatch.setattr(voice, "generate_text", _disabled, raising=False)
    monkeypatch.setattr(coverletter, "generate_text", _disabled, raising=False)


@pytest.fixture(autouse=True)
def _no_real_apply_ai_in_tests(monkeypatch):
    """Apply-pipeline engine calls default to a stub failure; tests re-patch."""
    from app.services import apply as apply_service
    from app.services import fillplan
    from app.services.claude import ClaudeError

    def _disabled(*args, **kwargs):
        raise ClaudeError("apply AI disabled in tests")

    monkeypatch.setattr(apply_service, "generate_json", _disabled)
    monkeypatch.setattr(fillplan, "generate_json", _disabled, raising=False)

    # The pipeline's best-effort research step must never reach a real engine
    # (web search on the user's key!). Stub apply's view of the research module;
    # research's own tests patch engine.generate_search themselves.
    from types import SimpleNamespace

    monkeypatch.setattr(
        apply_service, "research_service", SimpleNamespace(research_company=_disabled)
    )


@pytest.fixture(autouse=True)
def _no_real_import_ai_or_threads_in_tests(monkeypatch):
    """resume_import engine calls default to a stub failure; tests re-patch.
    The import route spawns a daemon run_import thread — stub it like apply's."""
    from app.services import resume_import
    from app.services.claude import ClaudeError

    def _disabled(*args, **kwargs):
        raise ClaudeError("import AI disabled in tests")

    monkeypatch.setattr(resume_import, "generate_text", _disabled)
    monkeypatch.setattr(resume_import, "generate_json", _disabled)
    monkeypatch.setattr(resume_import, "generate_json_with_image", _disabled)
    monkeypatch.setattr(resume_import, "start_run", lambda sid: None)


@pytest.fixture(autouse=True)
def _no_real_ats_ai_or_threads_in_tests(monkeypatch):
    """ats_scan engine calls default to a stub failure; tests re-patch. The
    ats route spawns a daemon run_scan thread — stub it like apply's."""
    from app.services import ats_scan
    from app.services.claude import ClaudeError

    def _disabled(*args, **kwargs):
        raise ClaudeError("ats AI disabled in tests")

    monkeypatch.setattr(ats_scan, "generate_json", _disabled)
    monkeypatch.setattr(ats_scan, "start_scan", lambda sid: None)


@pytest.fixture(autouse=True)
def _deterministic_ats_preflight_in_tests(monkeypatch):
    """create_scan's pre-flight gate (app.api.ats._preflight) reads ambient
    host state — engine_service.status() reflects backend/.env's
    ANTHROPIC_API_KEY and whether `claude`/`codex` are on PATH, and
    hiring_agent_available() reflects backend/.env's ATS_REPO_PATH. Both vary
    machine to machine, so left alone the gate is non-deterministic across
    dev boxes and CI. Pin both to a known "engine available, hiring-agent
    repo unset" default, matching every other AI-adjacent fixture in this
    file. Tests of the gate itself (test_ats_api.py's three preflight tests)
    override this per-test with their own monkeypatch calls, which win over
    this autouse default."""
    from types import SimpleNamespace

    from app.api import ats as ats_api

    monkeypatch.setattr(
        ats_api, "engine_service",
        SimpleNamespace(status=lambda: {"subscription_available": True, "api_key_configured": True}),
    )
    monkeypatch.setattr(settings, "ats_repo_path", None)


@pytest.fixture(autouse=True)
def _no_real_prep_ai_or_threads_in_tests(monkeypatch):
    """prep engine calls default to a stub failure; tests re-patch. The OA
    route spawns a daemon run_oa_research thread — stub it like ats_scan's."""
    from app.services import prep
    from app.services.claude import ClaudeError

    def _disabled(*args, **kwargs):
        raise ClaudeError("prep AI disabled in tests")

    monkeypatch.setattr(prep, "generate_json", _disabled)
    monkeypatch.setattr(prep, "generate_text", _disabled, raising=False)
    monkeypatch.setattr(prep, "generate_search", _disabled, raising=False)
    monkeypatch.setattr(prep, "start_oa_research", lambda sid: None, raising=False)


@pytest.fixture(autouse=True)
def _no_pipeline_threads_in_tests(monkeypatch):
    """The apply route spawns a daemon run_pipeline thread on session create and
    retry. A thread that leaks past monkeypatch teardown reconnects to the real
    SessionLocal and the real engine (observed as a live 401 to Anthropic mid-
    suite). Pipeline behavior is always tested by calling run_pipeline
    synchronously, so the thread spawn is never the subject — stub it globally."""
    from app.services import apply as apply_service

    monkeypatch.setattr(apply_service, "start_pipeline", lambda sid: None)


@pytest.fixture
def fake_embeddings(monkeypatch):
    """Deterministic bag-of-hashed-tokens embedder: shared words => higher cosine.
    Keeps the suite fast and model-free while exercising real ranking logic."""

    def fake_embed(text: str) -> bytes:
        vec = np.zeros(64, dtype=np.float32)
        for token in text.lower().split():
            vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % 64] += 1.0
        norm = np.linalg.norm(vec)
        if norm:
            vec /= norm
        return vec.tobytes()

    monkeypatch.setattr(embeddings, "embed_text", fake_embed)
    return fake_embed


@pytest.fixture
def db_sessionmaker(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False, "timeout": 15}
    )

    # Mirror app.db.base's production pragmas: Task 6's router tests spawn the real
    # daemon thread (apply.run_pipeline) and poll it concurrently from the request
    # thread, so the test engine needs the same WAL + busy_timeout as production or
    # it hits spurious "database is locked" failures production never would.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    Base.metadata.create_all(engine)
    test_sessionmaker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # apply.run_pipeline opens its own DB session (background-thread pattern, same as
    # mcp_server's SessionLocal) — redirect it to this test engine like test_mcp_tools.py
    # does for mcp_server.SessionLocal, or it silently binds to the real appbot.sqlite3.
    from app.services import apply as apply_service

    monkeypatch.setattr(apply_service, "SessionLocal", test_sessionmaker)
    # Same reasoning for app.api.network's discover/enrich background threads: the
    # brief's tests monkeypatch _run_discover_async/_run_enrich_async directly so
    # these threads never actually run today, but keep the real bodies redirected
    # to the test engine too so a future test that lets them run doesn't silently
    # write to the real appbot.sqlite3.
    from app.api import network as network_api

    monkeypatch.setattr(network_api, "SessionLocal", test_sessionmaker)
    # resume_import.run_import follows the same background-thread SessionLocal()
    # pattern as apply.run_pipeline; tests call it synchronously against
    # db_session, so it needs the same redirect or it silently binds to the
    # real appbot.sqlite3 and can never see the row the test just created.
    from app.services import resume_import

    monkeypatch.setattr(resume_import, "SessionLocal", test_sessionmaker)
    # ats_scan.run_scan follows the same background-thread SessionLocal()
    # pattern — redirect it or it binds to the real appbot.sqlite3.
    from app.services import ats_scan

    monkeypatch.setattr(ats_scan, "SessionLocal", test_sessionmaker)
    # prep.run_oa_research follows the same background-thread SessionLocal()
    # pattern — redirect it or it binds to the real appbot.sqlite3.
    from app.services import prep

    monkeypatch.setattr(prep, "SessionLocal", test_sessionmaker, raising=False)
    return test_sessionmaker


@pytest.fixture
def db_session(db_sessionmaker):
    db = db_sessionmaker()
    yield db
    db.close()


@pytest.fixture
def client(db_sessionmaker):
    def override_get_db():
        db = db_sessionmaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Real token auth in every test: the token file lands in tmp via data_dir.
    from app.services import auth

    with TestClient(app, headers={"X-Copilot-Token": auth.get_token()}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """The rate-limit buckets are process-global; refill them per test so a
    request-heavy test never starves the next one."""
    from app import main as app_main

    app_main.RATE_LIMIT.tokens = app_main.RATE_LIMIT.burst
    app_main.AUTH_FAIL_LIMIT.tokens = app_main.AUTH_FAIL_LIMIT.burst
    yield
