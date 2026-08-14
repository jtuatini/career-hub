"""One-page guarantee: tighten loop, hard fail with cleanup, approve guard."""

import pytest

from app.db.models import GeneratedDoc, Job, Resume
from app.services.latex import CompileInfo


@pytest.fixture
def resume_id(db_session):
    r = Resume(name="SWE", job_type="software", tex_source="\\documentclass{article}", page_count=1)
    db_session.add(r)
    db_session.commit()
    return r.id


def _fake_tailor(calls):
    from app.services.tailor import TailorResult

    def fake(tex, jd, memory_context="", extra_instruction=None, budget=None, allow_item_deletion=False):
        calls.append(extra_instruction)
        return TailorResult(tex=tex, applied=[], rejected=[])

    return fake


def _post(client, resume_id):
    return client.post(
        "/api/generate/tailor",
        json={"resume_id": resume_id, "company": "Acme", "title": "SWE", "jd_text": "Build things."},
    )


def test_hard_fail_after_max_rounds_cleans_up(client, db_session, monkeypatch, tmp_path, resume_id):
    from app.services import tailor_flow as tf

    calls: list = []
    monkeypatch.setattr(tf, "tailor_resume", _fake_tailor(calls))
    fake_pdf = tmp_path / "doc.pdf"

    def fake_compile(doc):
        fake_pdf.write_bytes(b"%PDF")
        doc.pdf_path = str(fake_pdf)
        return CompileInfo(2, 0.0)  # never fits

    monkeypatch.setattr(tf, "_compile_doc_info", fake_compile)
    resp = _post(client, resume_id)
    assert resp.status_code == 422
    assert "tighten rounds" in resp.json()["detail"]
    assert len(calls) == 1 + 3  # initial tailor + exactly 3 tighten rounds
    assert not fake_pdf.exists()  # PDF cleaned up
    assert db_session.query(GeneratedDoc).count() == 0  # row rolled back
    assert db_session.query(Job).count() == 1  # Job row survives


def test_loop_stops_as_soon_as_it_fits(client, db_session, monkeypatch, tmp_path, resume_id):
    from app.services import tailor_flow as tf

    calls: list = []
    monkeypatch.setattr(tf, "tailor_resume", _fake_tailor(calls))
    infos = iter([CompileInfo(2, 0.0), CompileInfo(1, 0.0)])  # overflow once, fit after round 1

    def fake_compile(doc):
        doc.pdf_path = str(tmp_path / "doc.pdf")
        return next(infos)

    monkeypatch.setattr(tf, "_compile_doc_info", fake_compile)
    resp = _post(client, resume_id)
    assert resp.status_code == 201
    assert resp.json()["page_count"] == 1
    assert len(calls) == 2  # initial + one tighten round only


def test_approve_rejects_overflowing_resume_doc(client, db_session, monkeypatch, tmp_path, resume_id):
    from app.api import docs as docs_api

    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF")
    job = client.post("/api/jobs", json={"company": "A", "title": "T", "jd_text": "x"}).json()
    doc = GeneratedDoc(
        job_id=job["id"], base_resume_id=resume_id, doc_type="resume",
        tex_source="x", pdf_path=str(pdf),
    )
    db_session.add(doc)
    db_session.commit()
    monkeypatch.setattr(docs_api, "pdf_page_count", lambda p: 2)
    resp = client.post(f"/api/docs/{doc.id}/approve")
    assert resp.status_code == 422
    db_session.refresh(doc)
    assert doc.approved is False

    monkeypatch.setattr(docs_api, "pdf_page_count", lambda p: 1)
    assert client.post(f"/api/docs/{doc.id}/approve").status_code == 200


def test_compile_error_during_tighten_cleans_up(client, db_session, monkeypatch, tmp_path, resume_id):
    from app.services import tailor_flow as tf
    from app.services.latex import CompileError

    calls: list = []
    monkeypatch.setattr(tf, "tailor_resume", _fake_tailor(calls))
    fake_pdf = tmp_path / "doc.pdf"
    compile_sequence = [
        ("success", CompileInfo(2, 0.0)),  # initial: succeeds, returns 2 pages (overflow)
        ("error", None),  # tighten round 1: raises CompileError
    ]
    sequence_iter = iter(compile_sequence)

    def fake_compile(doc):
        action, info = next(sequence_iter)
        if action == "success":
            fake_pdf.write_bytes(b"%PDF")
            doc.pdf_path = str(fake_pdf)
            return info
        else:  # error
            raise CompileError("Simulated failure during tighten")

    monkeypatch.setattr(tf, "_compile_doc_info", fake_compile)
    resp = _post(client, resume_id)
    assert resp.status_code == 422
    assert "LaTeX compile failed" in resp.json()["detail"]
    assert not fake_pdf.exists()  # PDF cleaned up on compile error
    assert db_session.query(GeneratedDoc).count() == 0  # row rolled back
    assert db_session.query(Job).count() == 1  # Job row survives
