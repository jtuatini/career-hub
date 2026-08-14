"""PDF→LaTeX import: stage machine, verification loop, acceptance, containment."""

from pathlib import Path

import pytest

from app.db.models import ImportSession, Resume
from app.services import resume_import
from app.services.latex import CompileError, CompileInfo, compile_tex_info

FAKE_TEXT = (
    "Jane Doe\njane@example.com | 555-0100\n"
    "University of Somewhere — BS Computer Science, GPA 3.8, May 2027\n"
    "Software Intern, Acme Corp, May 2025 - Aug 2025\n"
    "Built a data pipeline in Python that cut processing time 40%.\n"
) * 3  # comfortably past MIN_TEXT_CHARS
GOOD_TEX = "\\documentclass{article}\\begin{document}Jane Doe\\end{document}"
CLEAN_FIDELITY = {"dropped": [], "invented": [], "altered": []}
CLEAN_ALIGNMENT = {"issues": []}


def _mk(db, monkeypatch):
    monkeypatch.setattr(resume_import, "extract_text", lambda fn, data: FAKE_TEXT)
    monkeypatch.setattr(resume_import, "pdf_page_count", lambda p: 1)
    return resume_import.create_import(db, "jane.pdf", b"%PDF-fake", "Jane base", "software")


def _stub_clean_run(monkeypatch, tmp_path):
    pdf = tmp_path / "cand.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(resume_import, "generate_text", lambda s, u: GOOD_TEX)
    monkeypatch.setattr(
        resume_import, "compile_tex_info",
        lambda tex, out, name: (pdf, CompileInfo(page_count=1, overfull_vbox_pt=0.0)),
    )
    monkeypatch.setattr(resume_import, "generate_json", lambda s, u, sch: dict(CLEAN_FIDELITY))
    monkeypatch.setattr(resume_import, "_render_page_png", lambda p: b"\x89PNG-fake")
    monkeypatch.setattr(
        resume_import, "generate_json_with_image", lambda s, png, sch: dict(CLEAN_ALIGNMENT)
    )


def test_create_import_rejects_scanned_pdf(db_session, monkeypatch):
    monkeypatch.setattr(resume_import, "extract_text", lambda fn, data: "  \n ")
    with pytest.raises(ValueError, match="extractable text"):
        resume_import.create_import(db_session, "scan.pdf", b"%PDF-fake", "Scan", "software")


def test_clean_run_reaches_review_with_green_report(db_session, monkeypatch, tmp_path):
    s = _mk(db_session, monkeypatch)
    _stub_clean_run(monkeypatch, tmp_path)
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert s.status == "review" and s.stage == "review" and s.progress == 1.0
    assert s.state["report"] == {"fidelity": [], "fit": [], "alignment": []}
    assert s.state["tex"] == GOOD_TEX
    assert s.state["rounds"] == 0


def test_fidelity_finding_triggers_fix_round_then_converges(db_session, monkeypatch, tmp_path):
    s = _mk(db_session, monkeypatch)
    _stub_clean_run(monkeypatch, tmp_path)
    fidelity_calls = []

    def fidelity(system, user, schema):
        fidelity_calls.append(1)
        if len(fidelity_calls) == 1:
            return {"dropped": ["GPA 3.8"], "invented": [], "altered": []}
        return dict(CLEAN_FIDELITY)

    monkeypatch.setattr(resume_import, "generate_json", fidelity)
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert s.status == "review" and s.state["rounds"] == 1
    assert s.state["report"]["fidelity"] == []


def test_nonconvergent_run_surfaces_warnings_without_saving(db_session, monkeypatch, tmp_path):
    s = _mk(db_session, monkeypatch)
    _stub_clean_run(monkeypatch, tmp_path)
    monkeypatch.setattr(
        resume_import, "generate_json",
        lambda sy, u, sch: {"dropped": ["GPA 3.8"], "invented": [], "altered": []},
    )
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert s.status == "review"
    assert s.state["rounds"] == resume_import.MAX_FIX_ROUNDS
    assert "GPA 3.8" in s.state["report"]["fidelity"][0]
    assert s.resume_id is None
    assert db_session.query(Resume).count() == 0  # nothing auto-saved


def test_fit_failure_reported_when_pages_exceed_source(db_session, monkeypatch, tmp_path):
    s = _mk(db_session, monkeypatch)
    _stub_clean_run(monkeypatch, tmp_path)
    pdf = tmp_path / "cand.pdf"
    monkeypatch.setattr(
        resume_import, "compile_tex_info",
        lambda tex, out, name: (pdf, CompileInfo(page_count=2, overfull_vbox_pt=0.0)),
    )
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert any("2 page" in issue for issue in s.state["report"]["fit"])


def test_failed_fix_round_compile_clears_stale_preview_and_blocks_accept(db_session, monkeypatch, tmp_path):
    """A fix round whose corrected tex doesn't compile must not leave behind a
    candidate_pdf_path pointing at an earlier (different) tex's PDF, and the
    non-compiling tex must never be accept()-able."""
    s = _mk(db_session, monkeypatch)
    _stub_clean_run(monkeypatch, tmp_path)
    pdf = tmp_path / "cand.pdf"
    calls = []

    def flaky_compile(tex, out, name):
        calls.append(1)
        if len(calls) == 1:
            return pdf, CompileInfo(page_count=1, overfull_vbox_pt=0.0)
        raise CompileError("missing brace")

    monkeypatch.setattr(resume_import, "compile_tex_info", flaky_compile)
    # Force fix rounds every time so we run past the first (successful) compile.
    monkeypatch.setattr(
        resume_import, "generate_json",
        lambda s, u, sch: {"dropped": ["GPA 3.8"], "invented": [], "altered": []},
    )
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert s.status == "review"
    assert any(issue.startswith("does not compile") for issue in s.state["report"]["fit"])
    assert s.state["candidate_pdf_path"] is None
    with pytest.raises(ValueError):
        resume_import.accept(db_session, s)


def test_accept_creates_tailorable_root_resume(db_session, monkeypatch, tmp_path):
    s = _mk(db_session, monkeypatch)
    _stub_clean_run(monkeypatch, tmp_path)
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    monkeypatch.setattr(
        resume_import.resume_bank, "compile_and_store", lambda db, r: r
    )
    resume = resume_import.accept(db_session, s)
    assert resume.parent_id is None            # its own family root
    assert resume.tex_source == GOOD_TEX       # tailorable
    assert resume.job_type == "software" and resume.name == "Jane base"
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert s.status == "done" and s.resume_id == resume.id


def test_engine_failure_is_contained(db_session, monkeypatch):
    s = _mk(db_session, monkeypatch)
    # conftest leaves generate_text raising ClaudeError
    resume_import.run_import(s.id)
    db_session.expire_all()
    s = db_session.get(ImportSession, s.id)
    assert s.status == "error" and "disabled in tests" in s.error


def test_jakes_skeleton_compiles_with_real_pdflatex(tmp_path):
    filled = resume_import.JAKES_TEMPLATE.replace(
        "\\end{document}",
        "\\textbf{Jane Doe} \\\\ jane@example.com\n"
        "\\section{Education}\n\\resumeSubHeadingListStart\n"
        "\\resumeSubheading{University of Somewhere}{Somewhere, ST}"
        "{BS Computer Science}{Aug 2024 -- May 2028}\n"
        "\\resumeSubHeadingListEnd\n\\end{document}",
    )
    pdf, info = compile_tex_info(filled, tmp_path, "skeleton_smoke")
    assert pdf.exists() and info.page_count == 1
