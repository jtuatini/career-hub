"""Prep session endpoints. Engine stubbed per-test; OA thread stubbed autouse."""

import pytest

from app.db.models import Job, PrepSession
from app.services import prep


@pytest.fixture
def job_id(client):
    return client.post("/api/jobs", json={"company": "Umbra", "title": "SW Intern"}).json()["id"]


def test_start_interview_endpoint(client, job_id, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "First question?")
    r = client.post("/api/prep/sessions", json={"job_id": job_id, "kind": "interview"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "active"
    assert body["transcript"][0] == {"role": "interviewer", "text": "First question?"}


def test_start_unknown_job_404(client):
    r = client.post("/api/prep/sessions", json={"job_id": 999, "kind": "interview"})
    assert r.status_code == 404


def test_turn_and_finish(client, job_id, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q")
    sid = client.post("/api/prep/sessions", json={"job_id": job_id, "kind": "interview"}).json()["id"]
    r = client.post(f"/api/prep/sessions/{sid}/turn", json={"answer": "my answer"})
    assert r.status_code == 200 and len(r.json()["transcript"]) == 3
    debrief = {"strengths": [], "gaps": [], "suggested_answers": []}
    monkeypatch.setattr(prep, "generate_json", lambda s, u, schema: debrief)
    r = client.post(f"/api/prep/sessions/{sid}/finish")
    assert r.json()["status"] == "done" and r.json()["report"] == debrief


def test_turn_on_finished_session_409(client, job_id, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q")
    sid = client.post("/api/prep/sessions", json={"job_id": job_id, "kind": "interview"}).json()["id"]
    monkeypatch.setattr(prep, "generate_json", lambda s, u, schema: {"strengths": [], "gaps": [], "suggested_answers": []})
    client.post(f"/api/prep/sessions/{sid}/finish")
    r = client.post(f"/api/prep/sessions/{sid}/turn", json={"answer": "late"})
    assert r.status_code == 409


def test_start_oa_returns_running_row(client, job_id):
    # start_oa_research is stubbed autouse — the row stays running.
    r = client.post("/api/prep/sessions", json={"job_id": job_id, "kind": "oa"})
    assert r.status_code == 201
    assert r.json()["kind"] == "oa" and r.json()["status"] == "running"


def test_list_and_get_and_delete(client, job_id, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q")
    sid = client.post("/api/prep/sessions", json={"job_id": job_id, "kind": "interview"}).json()["id"]
    listed = client.get(f"/api/prep/sessions?job_id={job_id}").json()
    assert [s["id"] for s in listed] == [sid]
    assert client.get(f"/api/prep/sessions/{sid}").json()["id"] == sid
    assert client.delete(f"/api/prep/sessions/{sid}").status_code == 204
    assert client.get(f"/api/prep/sessions/{sid}").status_code == 404


def test_failed_first_question_persists_no_row(client, job_id, db_session, monkeypatch):
    def boom(s, u):
        raise RuntimeError("engine down")
    monkeypatch.setattr(prep, "generate_text", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/prep/sessions", json={"job_id": job_id, "kind": "interview"})
    db_session.expire_all()
    assert db_session.query(PrepSession).count() == 0  # flushed row was NOT committed
