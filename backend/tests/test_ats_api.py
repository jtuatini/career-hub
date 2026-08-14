"""ATS scan endpoints: lifecycle, target validation, capabilities."""

from types import SimpleNamespace

from app.db.models import AtsScan, GeneratedDoc, Job, Resume


def _mk_doc(db, jd="Python and Rust required."):
    job = Job(company="Acme", title="SWE Intern", jd_text=jd)
    db.add(job)
    db.commit()
    doc = GeneratedDoc(job_id=job.id, doc_type="resume", tex_source=r"\item Python tools")
    db.add(doc)
    db.commit()
    return doc


def _mk_resume(db, name="General"):
    resume = Resume(name=name, job_type="software", tex_source=r"\item Wrote C++ sims")
    db.add(resume)
    db.commit()
    return resume


def test_scan_requires_exactly_one_target(client):
    assert client.post("/api/ats/scan", json={"kind": "deep"}).status_code == 422
    assert (
        client.post("/api/ats/scan", json={"kind": "deep", "doc_id": 1, "resume_id": 1}).status_code
        == 422
    )


def test_scan_unknown_kind_rejected(client, db_session):
    doc = _mk_doc(db_session)
    r = client.post("/api/ats/scan", json={"kind": "voodoo", "doc_id": doc.id})
    assert r.status_code == 422


def test_scan_missing_target_404(client):
    r = client.post("/api/ats/scan", json={"kind": "deep", "doc_id": 99999})
    assert r.status_code == 404


def test_keyword_scan_completes_inline(client, db_session):
    doc = _mk_doc(db_session)  # no compiled PDF -> inline error result
    r = client.post("/api/ats/scan", json={"kind": "keyword", "doc_id": doc.id})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "error" and "PDF" in body["error"]


def test_ai_scan_creates_running_row_and_is_pollable(client, db_session):
    doc = _mk_doc(db_session)
    r = client.post("/api/ats/scan", json={"kind": "jd_match", "doc_id": doc.id})
    assert r.status_code == 201
    scan_id = r.json()["id"]
    assert r.json()["status"] == "running"  # start_scan stubbed by conftest
    poll = client.get(f"/api/ats/scan/{scan_id}")
    assert poll.status_code == 200 and poll.json()["kind"] == "jd_match"


def test_scan_list_returns_history_and_capabilities(client, db_session):
    doc = _mk_doc(db_session)
    client.post("/api/ats/scan", json={"kind": "keyword", "doc_id": doc.id})
    client.post("/api/ats/scan", json={"kind": "jd_match", "doc_id": doc.id})
    r = client.get(f"/api/ats/scans?doc_id={doc.id}")
    assert r.status_code == 200
    body = r.json()
    assert [s["kind"] for s in body["scans"]] == ["jd_match", "keyword"]  # newest first
    assert set(body["capabilities"]) == {"keyword", "jd_match", "deep", "hiring_agent"}


def test_scan_list_requires_exactly_one_target(client):
    assert client.get("/api/ats/scans").status_code == 422


def test_ai_scan_409s_without_engine(client, db_session, monkeypatch):
    from app.api import ats as ats_api

    monkeypatch.setattr(
        ats_api, "engine_service",
        SimpleNamespace(status=lambda: {"subscription_available": False, "api_key_configured": False}),
    )
    resume = _mk_resume(db_session)
    r = client.post("/api/ats/scan", json={"resume_id": resume.id, "kind": "deep"})
    assert r.status_code == 409
    assert "engine" in r.json()["detail"].lower()


def test_hiring_agent_409s_when_ollama_down(client, db_session, monkeypatch):
    from app.services import ats_scan as ats_service

    monkeypatch.setattr(ats_service, "hiring_agent_available", lambda: True)
    monkeypatch.setattr(ats_service, "ollama_running", lambda: False)
    resume = _mk_resume(db_session)
    r = client.post("/api/ats/scan", json={"resume_id": resume.id, "kind": "hiring_agent"})
    assert r.status_code == 409
    assert "ollama serve" in r.json()["detail"]


def test_cancel_running_scan_returns_cancelled(client, db_session):
    doc = _mk_doc(db_session)
    scan = AtsScan(doc_id=doc.id, kind="jd_match", status="running")
    db_session.add(scan)
    db_session.commit()
    r = client.post(f"/api/ats/scan/{scan.id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_done_scan_409s(client, db_session):
    doc = _mk_doc(db_session)
    scan = AtsScan(doc_id=doc.id, kind="jd_match", status="done")
    db_session.add(scan)
    db_session.commit()
    r = client.post(f"/api/ats/scan/{scan.id}/cancel")
    assert r.status_code == 409
    assert r.json()["detail"] == "Scan is not running"


def test_cancel_unknown_scan_404s(client):
    r = client.post("/api/ats/scan/999999/cancel")
    assert r.status_code == 404


def test_keyword_scan_needs_no_engine(client, db_session, monkeypatch):
    from app.api import ats as ats_api

    monkeypatch.setattr(
        ats_api, "engine_service",
        SimpleNamespace(status=lambda: {"subscription_available": False, "api_key_configured": False}),
    )
    resume = _mk_resume(db_session)
    r = client.post("/api/ats/scan", json={"resume_id": resume.id, "kind": "keyword"})
    assert r.status_code == 201
