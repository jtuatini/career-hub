"""Lineage-aware resume listing and deletion."""

from pathlib import Path

from app.db.models import GeneratedDoc, Resume


def _mk(db, name, job_type="software", parent_id=None, pdf_path=None):
    r = Resume(name=name, job_type=job_type, tex_source="\\documentclass{article}", parent_id=parent_id, pdf_path=pdf_path)
    db.add(r)
    db.commit()
    return r


def test_list_shows_only_lineage_heads_with_version_count(client, db_session):
    v1 = _mk(db_session, "SWE")
    v2 = _mk(db_session, "SWE", parent_id=v1.id)
    v3 = _mk(db_session, "SWE", parent_id=v2.id)
    other = _mk(db_session, "Quant", job_type="quant")
    listed = client.get("/api/resumes").json()
    assert {r["id"] for r in listed} == {v3.id, other.id}
    by_id = {r["id"]: r for r in listed}
    assert by_id[v3.id]["version_count"] == 3
    assert by_id[other.id]["version_count"] == 1


def test_delete_removes_entire_lineage_and_unlinks_docs(client, db_session, tmp_path):
    pdf = tmp_path / "v1.pdf"
    pdf.write_bytes(b"%PDF")
    v1 = _mk(db_session, "SWE", pdf_path=str(pdf))
    v2 = _mk(db_session, "SWE", parent_id=v1.id)
    job = client.post("/api/jobs", json={"company": "A", "title": "T", "jd_text": "x"}).json()
    doc = GeneratedDoc(job_id=job["id"], base_resume_id=v1.id, doc_type="resume", tex_source="x")
    db_session.add(doc)
    db_session.commit()

    # deleting the OLD version removes the whole family, not just that row
    assert client.delete(f"/api/resumes/{v1.id}").status_code == 204
    assert db_session.query(Resume).count() == 0
    db_session.refresh(doc)
    assert doc.base_resume_id is None
    assert not pdf.exists()


def test_delete_middle_version_removes_ancestors_and_descendants(client, db_session):
    v1 = _mk(db_session, "SWE")
    v2 = _mk(db_session, "SWE", parent_id=v1.id)
    v3 = _mk(db_session, "SWE", parent_id=v2.id)
    assert client.delete(f"/api/resumes/{v2.id}").status_code == 204
    assert db_session.query(Resume).count() == 0


def test_list_includes_pdf_only_resumes(client, db_session, tmp_path):
    pdf = tmp_path / "external.pdf"
    pdf.write_bytes(b"%PDF")
    # Create a LaTeX resume and a PDF-only resume
    latex = _mk(db_session, "SWE LaTeX")
    pdf_only = Resume(name="SWE PDF", job_type="software", tex_source=None, pdf_path=str(pdf))
    db_session.add(pdf_only)
    db_session.commit()

    listed = client.get("/api/resumes").json()
    assert {r["id"] for r in listed} == {latex.id, pdf_only.id}
    by_id = {r["id"]: r for r in listed}
    assert by_id[latex.id]["version_count"] == 1
    assert by_id[pdf_only.id]["version_count"] == 1
