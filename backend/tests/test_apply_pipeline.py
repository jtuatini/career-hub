"""ApplySession pipeline: stage machine, resumability, error containment."""

import pytest

from app.db.models import ApplySession, GeneratedDoc, Resume
from app.services import apply as apply_service

JD = "We need a software intern to build backend services in Python."


@pytest.fixture
def resume(db_session):
    r = Resume(name="SWE", job_type="software", tex_source="\\documentclass{article}", page_count=1)
    db_session.add(r)
    db_session.commit()
    return r


def _session(db):
    s = apply_service.create_session(db, "https://jobs.example/apply", JD, fields=[], buttons=[])
    return s


def _stub_happy_path(monkeypatch, db):
    from app.services import tailor_flow
    from app.services import apply as ap

    monkeypatch.setattr(
        ap.jobparse_service, "parse_posting",
        lambda text, url=None: {"company": "Acme", "title": "SWE Intern", "jd_text": JD, "location": "", "confidence": 0.9},
    )
    monkeypatch.setattr(ap, "generate_json", lambda *a, **k: {"job_type": "software"})

    def fake_tailor(db_, resume_, job_, jd_):
        doc = GeneratedDoc(job_id=job_.id, base_resume_id=resume_.id, doc_type="resume", tex_source="t")
        db_.add(doc)
        db_.commit()
        return tailor_flow.TailorOutcome(doc=doc, pages=1)

    monkeypatch.setattr(ap.tailor_flow, "tailor_to_doc", fake_tailor)
    monkeypatch.setattr(ap, "draft_cover_letter", lambda db_, job_, ctx: "Dear Acme, here is a letter.")
    monkeypatch.setattr(ap, "compile_tex", lambda tex, out, name: out / f"{name}.pdf")


def test_pipeline_happy_path(db_session, monkeypatch, tmp_path, resume):
    _stub_happy_path(monkeypatch, db_session)
    s = _session(db_session)
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.status == "running" and s.stage == "ready" and s.progress == 1.0
    assert s.job_id and s.resume_doc_id and s.cover_doc_id
    cover = db_session.get(GeneratedDoc, s.cover_doc_id)
    assert cover.approved is True and cover.vetted is False  # exemplar-excluded


def test_pipeline_error_is_contained_and_resumable(db_session, monkeypatch, resume):
    from app.services import apply as ap

    monkeypatch.setattr(
        ap.jobparse_service, "parse_posting",
        lambda text, url=None: {"company": "Acme", "title": "SWE", "jd_text": JD, "location": "", "confidence": 0.9},
    )
    # classify fails (conftest stub raises ClaudeError)
    s = _session(db_session)
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.status == "error" and s.stage == "classifying" and "disabled in tests" in s.error
    job_id_after_first_run = s.job_id
    assert job_id_after_first_run is not None  # parse stage survived

    # retry: classify now succeeds; pipeline resumes without re-parsing
    _stub_happy_path(monkeypatch, db_session)
    s.status = "running"
    s.error = None
    db_session.commit()
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.stage == "ready" and s.job_id == job_id_after_first_run


def test_page_overflow_becomes_friendly_stage_error(db_session, monkeypatch, resume):
    from app.services import apply as ap
    from app.services.tailor_flow import PageOverflowError

    monkeypatch.setattr(
        ap.jobparse_service, "parse_posting",
        lambda text, url=None: {"company": "Acme", "title": "SWE", "jd_text": JD, "location": "", "confidence": 0.9},
    )
    monkeypatch.setattr(ap, "generate_json", lambda *a, **k: {"job_type": "software"})

    def overflow(db_, resume_, job_, jd_):
        raise PageOverflowError(2, 1)

    monkeypatch.setattr(ap.tailor_flow, "tailor_to_doc", overflow)
    s = _session(db_session)
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.status == "error" and s.stage == "tailoring"
    assert "fit" in s.error.lower()


def test_no_latex_resume_in_bank_errors_cleanly(db_session, monkeypatch):
    from app.services import apply as ap

    monkeypatch.setattr(
        ap.jobparse_service, "parse_posting",
        lambda text, url=None: {"company": "Acme", "title": "SWE", "jd_text": JD, "location": "", "confidence": 0.9},
    )
    monkeypatch.setattr(ap, "generate_json", lambda *a, **k: {"job_type": "software"})
    s = _session(db_session)
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.status == "error" and "resume" in s.error.lower()


def test_pipeline_never_raises_even_if_error_path_itself_fails(db_session, monkeypatch, resume):
    """Double-fault case: the stage machine fails (classify, via the conftest's
    default disabled generate_json stub) AND recording that failure also fails
    (e.g. a genuinely locked sqlite file mid-rollback). run_pipeline must still
    return normally rather than let the second failure escape the daemon thread."""
    from app.services import apply as ap

    monkeypatch.setattr(
        ap.jobparse_service, "parse_posting",
        lambda text, url=None: {"company": "Acme", "title": "SWE", "jd_text": JD, "location": "", "confidence": 0.9},
    )
    # classify stage will fail via the conftest's default disabled generate_json stub.

    real_sessionmaker = ap.SessionLocal

    class BoomSession:
        """Wraps a real session but makes rollback() blow up, simulating the
        DB itself failing while the pipeline tries to record its own error."""

        def __init__(self):
            self._real = real_sessionmaker()

        def __getattr__(self, name):
            return getattr(self._real, name)

        def rollback(self):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(ap, "SessionLocal", BoomSession)

    s = _session(db_session)
    apply_service.run_pipeline(s.id)  # must not raise, even in this double-fault path

    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    # The error-write itself failed, so the row is left as it was at the last
    # successfully-committed stage rather than corrupted or advanced.
    assert s.status == "running"
    assert s.stage == "classifying"


def test_tailor_only_runs_tailor_and_skips_cover_letter(db_session, monkeypatch, resume):
    _stub_happy_path(monkeypatch, db_session)
    s = apply_service.create_session(
        db_session, "https://jobs.example/apply", JD, fields=[], buttons=[], mode="tailor_only"
    )
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.status == "running" and s.stage == "ready"
    assert s.resume_doc_id is not None
    assert s.cover_doc_id is None  # cover letter preset OFF
    assert s.state["fill_scope"] == "resume_slot_only"
    assert s.state["options"] == {
        "tailor_resume": True, "cover_letter": False, "answer_questions": False,
    }


def test_tailor_only_preset_wins_over_passed_options(db_session, monkeypatch, resume):
    """The popup's saved per-feature toggles must never re-enable cover letters
    or answers in a tailor-only run."""
    _stub_happy_path(monkeypatch, db_session)
    s = apply_service.create_session(
        db_session, "https://jobs.example/apply", JD, fields=[], buttons=[],
        mode="tailor_only",
        options={"tailor_resume": True, "cover_letter": True, "answer_questions": True},
    )
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.cover_doc_id is None
