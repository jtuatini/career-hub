"""Interview-chat sessions: context building, turns, debrief. Engine stubbed."""

import pytest

from app.db.models import DocType, GeneratedDoc, Job, PrepSession
from app.services import prep


@pytest.fixture
def job(db_session):
    j = Job(company="Umbra", title="Software Intern", jd_text="Build C++ tooling for satellites.")
    db_session.add(j)
    db_session.commit()
    return j


def test_start_interview_asks_first_question(db_session, job, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Tell me about a C++ project.")
    session = prep.start_interview(db_session, job.id)
    assert session.kind == "interview" and session.status == "active"
    assert session.transcript == [{"role": "interviewer", "text": "Tell me about a C++ project."}]


def test_start_interview_unknown_job(db_session):
    with pytest.raises(ValueError):
        prep.start_interview(db_session, 999)


def test_interview_context_includes_jd_and_tailored_resume(db_session, job, monkeypatch):
    db_session.add(GeneratedDoc(job_id=job.id, doc_type=DocType.RESUME,
                                tex_source="TEX-RESUME-BODY", approved=True))
    db_session.commit()
    seen = {}
    def fake_text(system, user_content):
        seen["user"] = user_content
        return "Q1"
    monkeypatch.setattr(prep, "generate_text", fake_text)
    prep.start_interview(db_session, job.id)
    assert "Build C++ tooling" in seen["user"]
    assert "TEX-RESUME-BODY" in seen["user"]


def test_answer_turn_appends_and_asks_next(db_session, job, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q1")
    session = prep.start_interview(db_session, job.id)
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Why Umbra?")
    session = prep.answer_turn(db_session, session.id, "I built a ray tracer.")
    assert [t["role"] for t in session.transcript] == ["interviewer", "candidate", "interviewer"]
    assert session.transcript[-1]["text"] == "Why Umbra?"


def test_answer_turn_transcript_reaches_engine(db_session, job, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q1")
    session = prep.start_interview(db_session, job.id)
    seen = {}
    def fake_text(system, user_content):
        seen["user"] = user_content
        return "Q2"
    monkeypatch.setattr(prep, "generate_text", fake_text)
    prep.answer_turn(db_session, session.id, "I built a ray tracer.")
    assert "ray tracer" in seen["user"] and "Q1" in seen["user"]


def test_answer_turn_rejects_finished_session(db_session, job, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q1")
    session = prep.start_interview(db_session, job.id)
    session.status = "done"
    db_session.commit()
    with pytest.raises(ValueError):
        prep.answer_turn(db_session, session.id, "hello?")


def test_finish_interview_stores_debrief(db_session, job, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q1")
    session = prep.start_interview(db_session, job.id)
    debrief = {"strengths": ["clear"], "gaps": ["metrics"],
               "suggested_answers": [{"question": "Q1", "points": ["quantify impact"]}]}
    monkeypatch.setattr(prep, "generate_json", lambda s, u, schema: debrief)
    session = prep.finish_interview(db_session, session.id)
    assert session.status == "done" and session.report == debrief


def test_failed_turn_leaves_session_retryable(db_session, job, monkeypatch):
    monkeypatch.setattr(prep, "generate_text", lambda s, u: "Q1")
    session = prep.start_interview(db_session, job.id)
    def boom(s, u):
        raise RuntimeError("engine down")
    monkeypatch.setattr(prep, "generate_text", boom)
    with pytest.raises(RuntimeError):
        prep.answer_turn(db_session, session.id, "answer")
    db_session.rollback()
    fresh = db_session.get(PrepSession, session.id)
    assert fresh.status == "active" and len(fresh.transcript) == 1  # unchanged, retry OK


def test_start_oa_creates_running_row(db_session, job):
    session = prep.start_oa(db_session, job.id)
    assert session.kind == "oa" and session.status == "running"


def test_run_oa_research_synthesizes_report(db_session, job, monkeypatch):
    session = prep.start_oa(db_session, job.id)
    monkeypatch.setattr(
        prep, "generate_search",
        lambda s, u, max_uses=8: ("Umbra uses HackerRank, graph problems.", ["https://x.test/1"]),
    )
    monkeypatch.setattr(prep, "_github_oa_snippets", lambda company: "### repo\nOA notes")
    report = {"summary": "s", "topics": ["graphs"], "links": ["https://x.test/1"],
              "sample_questions": [{"question": "Invert a tree", "source": "https://x.test/1"}]}
    seen = {}
    def fake_json(s, u, schema):
        seen["user"] = u
        return report
    monkeypatch.setattr(prep, "generate_json", fake_json)
    prep.run_oa_research(session.id)
    db_session.expire_all()
    fresh = db_session.get(PrepSession, session.id)
    assert fresh.status == "done" and fresh.report == report
    assert "HackerRank" in seen["user"] and "OA notes" in seen["user"]


def test_run_oa_research_lands_error_on_failure(db_session, job, monkeypatch):
    session = prep.start_oa(db_session, job.id)
    def boom(*a, **k):
        raise RuntimeError("search exploded")
    monkeypatch.setattr(prep, "generate_search", boom)
    prep.run_oa_research(session.id)  # must NOT raise
    db_session.expire_all()
    fresh = db_session.get(PrepSession, session.id)
    assert fresh.status == "error" and "search exploded" in fresh.error


def test_github_snippets_failure_is_nonfatal(db_session, job, monkeypatch):
    session = prep.start_oa(db_session, job.id)
    monkeypatch.setattr(prep, "generate_search", lambda s, u, max_uses=8: ("findings", []))
    def gh_boom(company):
        raise RuntimeError("github down")
    # _github_oa_snippets swallows its own errors; simulate at the httpx layer
    monkeypatch.setattr(prep.httpx, "get", gh_boom)
    monkeypatch.setattr(prep, "generate_json", lambda s, u, schema: {"summary": "s", "topics": [],
                                                                     "links": [], "sample_questions": []})
    prep.run_oa_research(session.id)
    db_session.expire_all()
    assert db_session.get(PrepSession, session.id).status == "done"
