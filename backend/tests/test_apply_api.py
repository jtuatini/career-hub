"""Apply API: session lifecycle over the pipeline service (thread stubbed out
globally in conftest)."""

from app.db.models import ApplySession, GeneratedDoc, Job, StyleProfile
from app.services import voice


def _create(client):
    return client.post(
        "/api/apply/sessions",
        json={"url": "https://jobs.x/1", "page_text": "We need an intern.", "fields": [], "buttons": []},
    )


def test_create_and_poll(client, db_session):
    resp = _create(client)
    assert resp.status_code == 201
    sid = resp.json()["id"]
    got = client.get(f"/api/apply/sessions/{sid}").json()
    assert got["status"] == "running" and got["stage"] == "parsing"
    assert client.get("/api/apply/sessions/99999").status_code == 404


def test_page_returns_actions(client, db_session, monkeypatch):
    from app.api import apply as apply_api

    sid = _create(client).json()["id"]
    monkeypatch.setattr(
        apply_api.fillplan, "build_plan",
        lambda db, s, fields, buttons: [{"kind": "fill", "index": 0, "value": "J", "review": False, "label": "x"}],
    )
    resp = client.post(f"/api/apply/sessions/{sid}/page", json={"url": "", "fields": [], "buttons": []})
    assert resp.status_code == 200
    assert resp.json()["actions"][0]["kind"] == "fill"


def test_report_saves_essay_edits_and_learns(client, db_session, monkeypatch):
    db_session.add(StyleProfile(content="## Tone\nDirect."))
    db_session.commit()
    sid = _create(client).json()["id"]
    learned = {}
    monkeypatch.setattr(
        voice, "learn_from_edit",
        lambda db, draft, final, ctx: learned.update(draft=draft, final=final) or {"added": 1},
    )
    resp = client.post(
        f"/api/apply/sessions/{sid}/report",
        json={
            "results": [{"index": 0, "status": "filled"}],
            "edits": [{"label": "Why us?", "draft": "AI draft.", "final": "My edited answer."}],
            "done": True,
        },
    )
    assert resp.status_code == 200
    assert learned == {"draft": "AI draft.", "final": "My edited answer."}
    s = db_session.get(ApplySession, sid)
    assert s.status == "done"
    listed = client.get("/api/qa").json()
    assert any(q["question"] == "Why us?" for q in listed)


def test_stop_and_retry(client, db_session):
    sid = _create(client).json()["id"]
    assert client.post(f"/api/apply/sessions/{sid}/stop").json()["status"] == "stopped"
    s = db_session.get(ApplySession, sid)
    s.status = "error"
    s.error = "boom"
    db_session.commit()
    assert client.post(f"/api/apply/sessions/{sid}/retry", json={}).status_code == 202
    db_session.refresh(s)
    assert s.status == "running" and s.error is None


def test_retry_with_resume_override(client, db_session):
    sid = _create(client).json()["id"]
    s = db_session.get(ApplySession, sid)
    # Stop the session first to avoid 409 guard
    client.post(f"/api/apply/sessions/{sid}/stop")
    s.status = "error"
    s.error = "boom"
    # Create a Job for the session
    job = Job(company="TestCo", title="Intern")
    db_session.add(job)
    db_session.flush()
    s.job_id = job.id
    # Pre-seed state and create a dummy GeneratedDoc
    s.state = {**s.state, "base_resume_id": 5}
    old_doc = GeneratedDoc(
        job_id=job.id,
        base_resume_id=5,
        doc_type="resume",
        tex_source="\\documentclass{article}\\end{document}",
        pdf_path="/tmp/test_resume.pdf",
    )
    db_session.add(old_doc)
    db_session.commit()
    s.resume_doc_id = old_doc.id
    db_session.commit()
    old_doc_id = old_doc.id
    # Retry with override
    resp = client.post(f"/api/apply/sessions/{sid}/retry", json={"resume_id": 7})
    assert resp.status_code == 202
    # Verify override is set and base_resume_id is cleared
    # Expire all cached objects to force fresh reads from DB
    db_session.expire_all()
    db_session.refresh(s)
    assert s.state["resume_override"] == 7
    assert s.state.get("base_resume_id") is None
    assert s.resume_doc_id is None
    # Verify old doc is deleted
    assert db_session.get(GeneratedDoc, old_doc_id) is None


def test_retry_running_session_returns_409(client, db_session):
    sid = _create(client).json()["id"]
    # Session starts in "running" status
    resp = client.post(f"/api/apply/sessions/{sid}/retry", json={})
    assert resp.status_code == 409
    assert "still running" in resp.json()["detail"]


def test_retry_on_tailor_only_session_ignores_options_and_keeps_preset(client, db_session):
    """tailor_only is a hard preset (cover_letter/answer_questions off) — a
    retry carrying options that would re-enable them must be silently
    ignored, or a cover letter could be generated on a résumé-only session."""
    resp = client.post(
        "/api/apply/sessions",
        json={
            "url": "https://jobs.x/2", "page_text": "We need an intern.",
            "fields": [], "buttons": [], "mode": "tailor_only",
        },
    )
    sid = resp.json()["id"]
    s = db_session.get(ApplySession, sid)
    assert s.state["options"] == {
        "tailor_resume": True, "cover_letter": False, "answer_questions": False,
    }
    client.post(f"/api/apply/sessions/{sid}/stop")
    s.status = "error"
    s.error = "boom"
    db_session.commit()

    resp = client.post(
        f"/api/apply/sessions/{sid}/retry",
        json={"options": {"tailor_resume": True, "cover_letter": True, "answer_questions": True}},
    )
    assert resp.status_code == 202
    db_session.expire_all()
    db_session.refresh(s)
    assert s.state["options"] == {
        "tailor_resume": True, "cover_letter": False, "answer_questions": False,
    }
