"""ats_scan service: kind dispatch, target resolution, error paths, wrap gating."""

from types import SimpleNamespace

from app.config import settings
from app.db.models import AtsScan, GeneratedDoc, Job, Resume
from app.services import ats_scan
from app.services.claude import ClaudeError


def _mk_doc(db, jd="We need Python and Rust experience."):
    job = Job(company="Acme", title="SWE Intern", jd_text=jd)
    db.add(job)
    db.commit()
    doc = GeneratedDoc(job_id=job.id, doc_type="resume", tex_source=r"\item Built Python tools")
    db.add(doc)
    db.commit()
    return doc


def _mk_scan(db, kind, doc_id=None, resume_id=None):
    scan = AtsScan(doc_id=doc_id, resume_id=resume_id, kind=kind)
    db.add(scan)
    db.commit()
    return scan


def test_jd_match_reports_engine_json_and_includes_jd(db_session, monkeypatch):
    doc = _mk_doc(db_session)
    scan = _mk_scan(db_session, "jd_match", doc_id=doc.id)
    seen = {}

    def fake_json(system, user, schema):
        seen["user"] = user
        return {"match_score": 72, "missing_keywords": ["rust"], "weak_areas": [],
                "suggestions": [], "summary": "ok"}

    monkeypatch.setattr(ats_scan, "generate_json", fake_json)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "done"
    assert scan.report["match_score"] == 72
    assert "Python and Rust" in seen["user"]      # JD made it into the prompt
    assert "Built Python tools" in seen["user"]   # tex fallback made it in


def test_jd_match_without_jd_is_an_error(db_session, monkeypatch):
    doc = _mk_doc(db_session, jd=None)
    scan = _mk_scan(db_session, "jd_match", doc_id=doc.id)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "error"
    assert "job description" in scan.error


def test_deep_scan_works_without_jd_on_bank_resume(db_session, monkeypatch):
    resume = Resume(name="General", job_type="software", tex_source=r"\item Wrote C++ sims")
    db_session.add(resume)
    db_session.commit()
    scan = _mk_scan(db_session, "deep", resume_id=resume.id)
    monkeypatch.setattr(
        ats_scan, "generate_json",
        lambda s, u, sc: {"open_source": {"score": 3, "evidence": "e"},
                          "self_projects": {"score": 5, "evidence": "e"},
                          "production": {"score": 4, "evidence": "e"},
                          "technical_skills": {"score": 6, "evidence": "e"},
                          "bonus": [], "deductions": [], "overall_score": 18, "summary": "s"},
    )
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "done" and scan.report["overall_score"] == 18


def test_engine_failure_lands_on_the_row(db_session, monkeypatch):
    doc = _mk_doc(db_session)
    scan = _mk_scan(db_session, "deep", doc_id=doc.id)

    def boom(*a, **k):
        raise ClaudeError("engine down")

    monkeypatch.setattr(ats_scan, "generate_json", boom)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "error" and "engine down" in scan.error


def test_run_scan_on_already_cancelled_row_makes_no_engine_call(db_session, monkeypatch):
    doc = _mk_doc(db_session)
    scan = _mk_scan(db_session, "jd_match", doc_id=doc.id)
    scan.status = "cancelled"
    db_session.commit()

    def boom(*a, **k):
        raise AssertionError("engine must not be called for a scan that isn't running")

    monkeypatch.setattr(ats_scan, "generate_json", boom)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "cancelled"
    assert scan.report is None


def test_run_scan_does_not_resurrect_a_row_cancelled_mid_flight(db_session, monkeypatch):
    doc = _mk_doc(db_session)
    scan = _mk_scan(db_session, "jd_match", doc_id=doc.id)  # starts "running"

    def fake_json(system, user, schema):
        # Simulate a concurrent POST /cancel landing on a different session
        # while this (the background thread's) engine call is still in flight.
        scan.status = "cancelled"
        db_session.commit()
        return {"match_score": 72, "missing_keywords": [], "weak_areas": [],
                "suggestions": [], "summary": "ok"}

    monkeypatch.setattr(ats_scan, "generate_json", fake_json)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "cancelled"
    assert scan.report is None


def test_keyword_scan_without_pdf_is_an_error(db_session):
    doc = _mk_doc(db_session)
    scan = _mk_scan(db_session, "keyword", doc_id=doc.id)
    ats_scan.run_keyword(db_session, scan)
    assert scan.status == "error" and "PDF" in scan.error


def test_keyword_scan_extraction_failure_lands_on_the_row(db_session, tmp_path, monkeypatch):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"not actually a pdf")
    doc = _mk_doc(db_session)
    doc.pdf_path = str(pdf)
    db_session.commit()
    scan = _mk_scan(db_session, "keyword", doc_id=doc.id)
    assert scan.status == "running"  # default, before the scan runs

    def boom(*a, **k):
        raise ValueError("corrupt PDF")

    monkeypatch.setattr(ats_scan, "ats_report", boom)
    ats_scan.run_keyword(db_session, scan)  # must not raise/500
    db_session.refresh(scan)
    assert scan.status == "error" and "corrupt PDF" in scan.error


def test_hiring_agent_available_requires_config_and_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ats_repo_path", "")
    assert ats_scan.hiring_agent_available() is False
    monkeypatch.setattr(settings, "ats_repo_path", str(tmp_path))
    assert ats_scan.hiring_agent_available() is False  # no score.py / venv
    (tmp_path / "score.py").write_text("")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    assert ats_scan.hiring_agent_available() is True


def test_hiring_agent_unconfigured_scan_errors(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ats_repo_path", "")
    doc = _mk_doc(db_session)
    scan = _mk_scan(db_session, "hiring_agent", doc_id=doc.id)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "error" and "ATS_REPO_PATH" in scan.error


def test_hiring_agent_wraps_subprocess_stdout(db_session, tmp_path, monkeypatch):
    repo = tmp_path / "hiring-agent"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")
    (repo / "score.py").write_text("")
    monkeypatch.setattr(settings, "ats_repo_path", str(repo))
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    doc = _mk_doc(db_session)
    doc.pdf_path = str(pdf)
    db_session.commit()
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["cwd"] = argv, kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout="OVERALL SCORE: 31/40", stderr="")

    monkeypatch.setattr(ats_scan.subprocess, "run", fake_run)
    scan = _mk_scan(db_session, "hiring_agent", doc_id=doc.id)
    ats_scan.run_scan(scan.id)
    db_session.refresh(scan)
    assert scan.status == "done"
    assert scan.report["raw_output"] == "OVERALL SCORE: 31/40"
    assert seen["argv"][1] == "score.py" and str(repo) == str(seen["cwd"])


def test_capabilities_shape(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ats_repo_path", "")
    doc = _mk_doc(db_session)
    caps = ats_scan.capabilities(db_session, doc_id=doc.id, resume_id=None)
    assert caps == {"keyword": False, "jd_match": True, "deep": True, "hiring_agent": False}


def _minimal_deep_report():
    return {
        "open_source": {"score": 3, "evidence": "e"},
        "self_projects": {"score": 5, "evidence": "e"},
        "production": {"score": 4, "evidence": "e"},
        "technical_skills": {"score": 6, "evidence": "e"},
        "bonus": [], "deductions": [], "overall_score": 18, "summary": "s",
    }


def _make_deep_scan(db):
    doc = _mk_doc(db)
    return _mk_scan(db, "deep", doc_id=doc.id)


def test_deep_scan_includes_real_github_evidence(db_session, monkeypatch):
    from app.services import ats_scan
    monkeypatch.setattr(
        ats_scan, "_github_evidence",
        lambda db: "### jared/raytracer (https://github.com/x)\nC++ path tracer. Language: C++.",
    )
    seen = {}

    def fake_json(system, user, schema):
        seen["user"] = user
        return _minimal_deep_report()

    monkeypatch.setattr(ats_scan, "generate_json", fake_json)
    scan = _make_deep_scan(db_session)
    ats_scan.run_scan(scan.id)
    assert "raytracer" in seen["user"]
    assert "ACTUAL PUBLIC GITHUB" in seen["user"]


def test_deep_scan_notes_when_github_unavailable(db_session, monkeypatch):
    from app.services import ats_scan
    monkeypatch.setattr(ats_scan, "_github_evidence", lambda db: "")
    seen = {}

    def fake_json(system, user, schema):
        seen["user"] = user
        return _minimal_deep_report()

    monkeypatch.setattr(ats_scan, "generate_json", fake_json)
    scan = _make_deep_scan(db_session)
    ats_scan.run_scan(scan.id)
    assert "not available for verification" in seen["user"]


def test_github_evidence_swallows_network_failure(db_session, monkeypatch):
    from app.services import ats_scan

    def boom(*a, **k):
        raise RuntimeError("net down")

    monkeypatch.setattr(ats_scan, "_fetch_repos", boom)
    monkeypatch.setattr(ats_scan, "load_profile", lambda db: {"github": "someuser"})
    assert ats_scan._github_evidence(db_session) == ""
