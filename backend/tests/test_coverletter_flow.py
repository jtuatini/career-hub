"""Draft-first cover letters: generate editable text, finalize compiles + learns."""

import pytest

from app.db.models import GeneratedDoc, StyleProfile
from app.services import coverletter, voice

BODY = "I want to build things at Acme this summer. Here is why I would be useful to the team."


@pytest.fixture
def job_id(client):
    return client.post(
        "/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "Build software with us."}
    ).json()["id"]


def _gen(client, job_id, monkeypatch):
    monkeypatch.setattr(coverletter, "generate_text", lambda *a, **k: BODY)
    return client.post("/api/generate/cover-letter", json={"job_id": job_id})


def test_generate_returns_draft_without_compiling(client, job_id, monkeypatch):
    resp = _gen(client, job_id, monkeypatch)
    assert resp.status_code == 201
    data = resp.json()
    assert data["body_text"] == BODY
    assert data["page_count"] == 0
    assert client.get(f"/api/docs/{data['id']}/pdf").status_code == 404  # nothing compiled


def test_critique_refine_applied_when_profile_exists(client, job_id, monkeypatch, db_session):
    db_session.add(StyleProfile(content="## Tone\nDirect."))
    db_session.commit()
    monkeypatch.setattr(coverletter, "generate_text", lambda *a, **k: BODY)
    monkeypatch.setattr(voice, "generate_json", lambda *a, **k: {"revised": "Refined: " + BODY})
    resp = client.post("/api/generate/cover-letter", json={"job_id": job_id})
    assert resp.json()["body_text"].startswith("Refined:")


def test_body_edit_then_finalize_compiles_and_learns(client, job_id, monkeypatch, db_session, tmp_path):
    doc_id = _gen(client, job_id, monkeypatch).json()["id"]
    edited = BODY + " Also, I ship fast."
    assert client.put(f"/api/docs/{doc_id}/body", json={"body_text": edited}).status_code == 200

    learned = {}

    def fake_learn(db, draft, final, context):
        learned.update(draft=draft, final=final, context=context)
        return {"added": 1}

    monkeypatch.setattr(voice, "learn_from_edit", fake_learn)
    # avoid a real LaTeX toolchain in tests: stub the compile
    from app.api import docs as docs_api

    monkeypatch.setattr(docs_api, "compile_tex", lambda tex, out_dir, name: tmp_path / "cl.pdf")
    monkeypatch.setattr(docs_api, "pdf_page_count", lambda path: 1)
    (tmp_path / "cl.pdf").write_bytes(b"%PDF-1.4 fake")

    resp = client.post(f"/api/docs/{doc_id}/finalize")
    assert resp.status_code == 200
    assert resp.json()["approved"] is True and resp.json()["page_count"] == 1
    assert learned["draft"] == BODY and learned["final"] == edited

    # editing after approval is locked
    assert client.put(f"/api/docs/{doc_id}/body", json={"body_text": "x"}).status_code == 409


def test_finalize_rejects_resume_docs(client, db_session, job_id):
    doc = GeneratedDoc(job_id=job_id, doc_type="resume", tex_source="\\documentclass{article}")
    db_session.add(doc)
    db_session.commit()
    assert client.post(f"/api/docs/{doc.id}/finalize").status_code == 422


def test_approve_rejects_draft_cover_letters(client, db_session, job_id):
    """The generic /approve must not let a draft cover letter (empty tex_source,
    no PDF) become a permanently-stuck, never-vetted exemplar — that's finalize's job."""
    doc = GeneratedDoc(job_id=job_id, doc_type="cover_letter", tex_source="", body_text=BODY)
    db_session.add(doc)
    db_session.commit()
    resp = client.post(f"/api/docs/{doc.id}/approve")
    assert resp.status_code == 422
    db_session.refresh(doc)
    assert doc.approved is False


def test_exemplars_included_once_approved_letters_exist(client, job_id, monkeypatch, db_session):
    db_session.add(
        GeneratedDoc(job_id=job_id, doc_type="cover_letter", tex_source="", body_text="Past approved letter body.", approved=True, vetted=True)
    )
    db_session.commit()
    seen = {}

    def fake_gen(system, user_content, max_tokens=16000):
        seen["system"] = system
        return BODY

    monkeypatch.setattr(coverletter, "generate_text", fake_gen)
    client.post("/api/generate/cover-letter", json={"job_id": job_id})
    assert "Past approved letter body." in seen["system"]


def test_build_cover_letter_tex_compiles_with_special_chars(tmp_path):
    """Escape-then-compile path, unstubbed: the only place build_cover_letter_tex's
    output is verified against a real pdflatex run (mirrors test_latex.py's
    real-compile tests)."""
    from app.services.latex import compile_tex, pdf_page_count

    body = (
        "I grew revenue 40% for Smith & Sons. Rate: #1 in my cohort. "
        "Reach me at jared_t@example.com ($50 referral bonus mentioned in the posting)."
    )
    tex = coverletter.build_cover_letter_tex("Smith & Sons", body)

    pdf_path = compile_tex(tex, tmp_path, "cover_letter")
    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"
    assert pdf_page_count(pdf_path) >= 1
