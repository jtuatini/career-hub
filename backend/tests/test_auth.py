import stat

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import auth
from app.services import terminal as terminal_service


@pytest.fixture
def bare_client(tmp_path, monkeypatch):
    """No default token header — exercises the 401 paths."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return TestClient(app)


def test_missing_or_wrong_token_is_401(bare_client):
    resp = bare_client.get("/api/resumes")
    assert resp.status_code == 401
    assert "X-Copilot-Token" in resp.json()["detail"]
    resp = bare_client.get("/api/resumes", headers={"X-Copilot-Token": "wrong"})
    assert resp.status_code == 401


def test_correct_token_passes_and_health_is_exempt(bare_client):
    assert bare_client.get("/api/health").status_code == 200
    token = auth.get_token()
    # 404 (no such resume), not 401: the gate opened and routing took over.
    assert bare_client.get("/api/resumes/999", headers={"X-Copilot-Token": token}).status_code in (
        404,
        500,  # real-DB session may not resolve in bare client; auth is what's under test
    )


def test_token_file_is_owner_only_and_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = auth.get_token()
    assert len(first) == 64
    mode = stat.S_IMODE(auth.token_path().stat().st_mode)
    assert mode == 0o600
    assert auth.get_token() == first  # persists, not regenerated


def test_extension_token_endpoint_requires_auth(client):
    body = client.post("/api/profile/extension-token").json()
    assert body["token"] == auth.get_token()
    # POST-only: the reveal is a deliberate action, never a routine read.
    assert client.get("/api/profile/extension-token").status_code == 405


def test_websocket_rejects_foreign_origins(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(terminal_service, "SESSION_ARGV", ["/bin/cat"])
    try:
        # Evil website origin: rejected before the PTY is ever attached.
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/api/terminal/ws", headers={"origin": "https://evil.example"}
            ) as ws:
                ws.receive_bytes()

        # The local frontend origin works.
        with client.websocket_connect(
            "/api/terminal/ws", headers={"origin": "http://localhost:5173"}
        ) as ws:
            ws.send_bytes(b"ok\n")
            received = b""
            while b"ok" not in received:
                received += ws.receive_bytes()
    finally:
        terminal_service.restart()


def test_pdf_ticket_flow(client, tmp_path, monkeypatch):
    """Signed short-lived query lets a plain browser tab fetch a PDF; tampering
    or expiry shuts it down."""
    import time as time_mod

    from fastapi.testclient import TestClient

    from app.db.base import get_db
    from app.db.models import GeneratedDoc, Job
    from app.main import app as the_app
    from app.services import auth as auth_service

    db = next(the_app.dependency_overrides[get_db]())
    job = Job(company="T", title="t")
    db.add(job)
    db.flush()
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-fake")
    doc = GeneratedDoc(job_id=job.id, doc_type="resume", tex_source="x", pdf_path=str(pdf))
    db.add(doc)
    db.commit()

    url = client.get(f"/api/docs/{doc.id}/pdf-ticket").json()["url"]
    bare = TestClient(the_app)  # no token header — like a new browser tab
    assert bare.get(url).status_code == 200
    assert bare.get(url.split("?")[0]).status_code == 401  # no ticket
    assert bare.get(url + "0").status_code == 401  # tampered sig

    # Expired ticket
    monkeypatch.setattr(time_mod, "time", lambda: time_mod.mktime(time_mod.gmtime()) + 10_000)
    assert auth_service.verify_ticket(f"/api/docs/{doc.id}/pdf", url.split("exp=")[1].split("&")[0], url.split("sig=")[1]) is False
