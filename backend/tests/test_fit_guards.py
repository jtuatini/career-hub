"""Fit estimator wired as guards on the tex-write paths."""

from app.db.models import DocType, GeneratedDoc, Job
from app.services import fit
from app.services.latex import CompileInfo


def _doc_row(db):
    job = Job(company="A", title="B")
    db.add(job)
    db.flush()
    d = GeneratedDoc(job_id=job.id, doc_type=DocType.RESUME, tex_source="x")
    db.add(d)
    db.commit()
    return d


def _stub_compile(monkeypatch, tmp_path, pages=1):
    """Same stubbing approach as test_resume_tex_edit.py's _stub_compile:
    patch the names docs.py imported into its own module namespace so the
    PUT reaches the warning path without a real pdflatex compile."""
    from app.api import docs as docs_api

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(docs_api, "compile_tex", lambda tex, out_dir, name: pdf)
    monkeypatch.setattr(docs_api, "pdf_page_count", lambda path: pages)
    return pdf


def _fake_compile_info(pages: int) -> CompileInfo:
    return CompileInfo(page_count=pages, overfull_vbox_pt=0.0)


def test_fit_endpoint_reports_sections(client, db_session):
    d = _doc_row(db_session)
    r = client.get(f"/api/docs/{d.id}/fit")
    assert r.status_code == 200
    body = r.json()
    assert {"lines", "budget", "fits", "sections"} <= body.keys()


def test_tex_update_warns_on_predicted_overflow(client, db_session, monkeypatch, tmp_path):
    d = _doc_row(db_session)
    from app.api import docs as docs_api

    _stub_compile(monkeypatch, tmp_path)
    monkeypatch.setattr(
        docs_api.fit, "estimate",
        lambda tex: fit.FitReport(lines=90, budget=54, fits=False,
                                  sections=[{"name": "Projects", "lines": 60}]),
    )
    r = client.put(
        f"/api/docs/{d.id}/tex",
        json={"tex_source": "\\documentclass{article}\\begin{document}edited\\end{document}"},
    )
    assert r.status_code == 200
    assert any("Fit estimate" in w for w in r.json()["warnings"])


def test_describe_overflow_names_biggest_section(monkeypatch):
    from app.services import tailor_flow

    report = fit.FitReport(lines=90, budget=54, fits=False,
                           sections=[{"name": "Projects", "lines": 60}])
    monkeypatch.setattr(tailor_flow.fit, "estimate", lambda tex: report)
    desc = tailor_flow._describe_overflow_with_fit("SOME TEX", _fake_compile_info(pages=2), 1)
    assert "Projects" in desc
