"""Amend endpoint + vetted exemplar gating."""

import pytest

from app.db.models import GeneratedDoc, StyleProfile
from app.services import coverletter, voice


@pytest.fixture
def job_id(client):
    return client.post(
        "/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "Build software."}
    ).json()["id"]


def _mk_letter(db, job_id, *, approved=True, vetted=False, body="Original body text."):
    doc = GeneratedDoc(
        job_id=job_id, doc_type="cover_letter", tex_source="",
        draft_text=body, body_text=body, approved=approved, vetted=vetted,
    )
    db.add(doc)
    db.commit()
    return doc


def test_amend_recompiles_learns_and_vets(client, db_session, job_id, monkeypatch, tmp_path):
    from app.api import docs as docs_api

    doc = _mk_letter(db_session, job_id)
    learned = {}
    monkeypatch.setattr(
        voice, "learn_from_edit",
        lambda db, draft, final, ctx: learned.update(draft=draft, final=final) or {"added": 1},
    )
    monkeypatch.setattr(docs_api, "compile_tex", lambda tex, out, name: tmp_path / "cl.pdf")
    monkeypatch.setattr(docs_api, "pdf_page_count", lambda p: 1)
    (tmp_path / "cl.pdf").write_bytes(b"%PDF")

    resp = client.post(f"/api/docs/{doc.id}/amend", json={"body_text": "Edited body, much better."})
    assert resp.status_code == 200
    assert resp.json()["vetted"] is True
    db_session.refresh(doc)
    assert doc.vetted is True and doc.body_text == "Edited body, much better."
    assert learned["draft"] == "Original body text." and learned["final"] == "Edited body, much better."


def test_amend_rejects_unfinalized_and_non_letters(client, db_session, job_id):
    draft = _mk_letter(db_session, job_id, approved=False)
    assert client.post(f"/api/docs/{draft.id}/amend", json={"body_text": "x"}).status_code == 409
    resume = GeneratedDoc(job_id=job_id, doc_type="resume", tex_source="x")
    db_session.add(resume)
    db_session.commit()
    assert client.post(f"/api/docs/{resume.id}/amend", json={"body_text": "x"}).status_code == 422


def test_unvetted_letters_are_not_exemplars(client, db_session, job_id, monkeypatch):
    _mk_letter(db_session, job_id, vetted=False, body="UNVETTED PIPELINE LETTER")
    _mk_letter(db_session, job_id, vetted=True, body="VETTED HUMAN LETTER")
    seen = {}

    def fake_gen(system, user_content, max_tokens=16000):
        seen["system"] = system
        return "body"

    monkeypatch.setattr(coverletter, "generate_text", fake_gen)
    client.post("/api/generate/cover-letter", json={"job_id": job_id})
    assert "VETTED HUMAN LETTER" in seen["system"]
    assert "UNVETTED PIPELINE LETTER" not in seen["system"]


def test_finalize_sets_vetted(client, db_session, job_id, monkeypatch, tmp_path):
    from app.api import docs as docs_api

    doc = _mk_letter(db_session, job_id, approved=False)
    monkeypatch.setattr(docs_api, "compile_tex", lambda tex, out, name: tmp_path / "cl.pdf")
    monkeypatch.setattr(docs_api, "pdf_page_count", lambda p: 1)
    (tmp_path / "cl.pdf").write_bytes(b"%PDF")
    assert client.post(f"/api/docs/{doc.id}/finalize").status_code == 200
    db_session.refresh(doc)
    assert doc.vetted is True
