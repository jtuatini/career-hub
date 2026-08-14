"""Hand-editing a tailored resume's LaTeX in the DB: PUT /api/docs/{id}/tex."""

import pytest

from app.db.models import GeneratedDoc, Resume
from app.services.latex import CompileError


@pytest.fixture
def resume_doc(client, db_session):
    job = client.post(
        "/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "x"}
    ).json()
    base = Resume(name="Base", job_type="swe", tex_source="\\documentclass{article}", page_count=1)
    db_session.add(base)
    db_session.flush()
    doc = GeneratedDoc(
        job_id=job["id"],
        base_resume_id=base.id,
        doc_type="resume",
        tex_source="\\documentclass{article}\\begin{document}old\\end{document}",
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def _stub_compile(monkeypatch, tmp_path, pages=1):
    from app.api import docs as docs_api

    pdf = tmp_path / "edited.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(docs_api, "compile_tex", lambda tex, out_dir, name: pdf)
    monkeypatch.setattr(docs_api, "pdf_page_count", lambda path: pages)
    return pdf


NEW_TEX = "\\documentclass{article}\\begin{document}edited\\end{document}"


def test_edit_persists_tex_and_recompiles(client, db_session, resume_doc, monkeypatch, tmp_path):
    pdf = _stub_compile(monkeypatch, tmp_path)
    r = client.put(f"/api/docs/{resume_doc.id}/tex", json={"tex_source": NEW_TEX})
    assert r.status_code == 200
    assert r.json() == {"id": resume_doc.id, "page_count": 1, "approved": False, "warnings": []}
    db_session.refresh(resume_doc)
    assert resume_doc.tex_source == NEW_TEX
    assert resume_doc.pdf_path == str(pdf)


def test_compile_failure_persists_nothing(client, db_session, resume_doc, monkeypatch):
    from app.api import docs as docs_api

    def boom(tex, out_dir, name):
        raise CompileError("! Undefined control sequence")

    monkeypatch.setattr(docs_api, "compile_tex", boom)
    old_tex, old_pdf = resume_doc.tex_source, resume_doc.pdf_path
    r = client.put(f"/api/docs/{resume_doc.id}/tex", json={"tex_source": NEW_TEX})
    assert r.status_code == 422
    assert "compile failed" in r.json()["detail"]
    db_session.refresh(resume_doc)
    assert resume_doc.tex_source == old_tex
    assert resume_doc.pdf_path == old_pdf


def test_overflow_revokes_approval(client, db_session, resume_doc, monkeypatch, tmp_path):
    resume_doc.approved = True
    db_session.commit()
    _stub_compile(monkeypatch, tmp_path, pages=2)
    r = client.put(f"/api/docs/{resume_doc.id}/tex", json={"tex_source": NEW_TEX})
    assert r.status_code == 200
    body = r.json()
    assert body["approved"] is False
    assert body["page_count"] == 2
    assert any("approval revoked" in w for w in body["warnings"])
    db_session.refresh(resume_doc)
    assert resume_doc.approved is False


def test_overflow_unapproved_warns_but_saves(client, db_session, resume_doc, monkeypatch, tmp_path):
    _stub_compile(monkeypatch, tmp_path, pages=3)
    r = client.put(f"/api/docs/{resume_doc.id}/tex", json={"tex_source": NEW_TEX})
    assert r.status_code == 200
    assert any("not approvable" in w for w in r.json()["warnings"])
    db_session.refresh(resume_doc)
    assert resume_doc.tex_source == NEW_TEX


def test_cover_letter_rejected(client, db_session, resume_doc):
    cl = GeneratedDoc(job_id=resume_doc.job_id, doc_type="cover_letter", tex_source="", body_text="hi")
    db_session.add(cl)
    db_session.commit()
    r = client.put(f"/api/docs/{cl.id}/tex", json={"tex_source": NEW_TEX})
    assert r.status_code == 422
    assert "cover letters use /body" in r.json()["detail"]


def test_empty_tex_and_missing_doc_rejected(client, resume_doc):
    assert (
        client.put(f"/api/docs/{resume_doc.id}/tex", json={"tex_source": "   "}).status_code == 422
    )
    assert client.put("/api/docs/999999/tex", json={"tex_source": NEW_TEX}).status_code == 404
