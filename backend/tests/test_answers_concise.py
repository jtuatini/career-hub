"""Application answers: concise-by-default drafting, cache-only research grounding,
and the pipeline's best-effort research step."""

from types import SimpleNamespace

import pytest

from app.db.models import ApplySession, CompanyResearch, GeneratedDoc, Resume
from app.services import answers as answers_service
from app.services import apply as apply_service

JD = "We need a software intern to build backend services in Python."


@pytest.fixture
def capture_gen(monkeypatch):
    seen = {}

    def fake_gen(system, user_content, **kw):
        seen["system"] = system
        seen["user_content"] = user_content
        return "Short answer."

    monkeypatch.setattr(answers_service, "generate_text", fake_gen)
    return seen


def test_prompt_demands_brevity(client, db_session, capture_gen):
    answers_service.draft_answer(db_session, "Why do you want to work here?")
    assert "40-90 words" in capture_gen["system"]
    assert "120-200" not in capture_gen["system"]
    assert "ONE specific detail" in capture_gen["system"]


def test_stated_word_floor_detection():
    cases = {
        "In 250-400 words, tell us about a challenge.": 250,
        "In 250–400 words, describe X.": 250,
        "Write 250 to 400 words on Y.": 250,
        "Please write at least 150 words.": 150,
        "Minimum of 100 words.": 100,
        "200 words minimum, please.": 200,
        "Why do you want to work here?": None,
        "Describe your 3 favorite projects.": None,
    }
    for question, want in cases.items():
        assert answers_service.stated_word_floor(question) == want, question


def test_word_floor_reaches_the_prompt(client, db_session, capture_gen):
    answers_service.draft_answer(db_session, "In 250-400 words, tell us about a hard problem.")
    assert "at least 250 words" in capture_gen["user_content"]
    answers_service.draft_answer(db_session, "Why us?")
    assert "LENGTH REQUIREMENT" not in capture_gen["user_content"]


def test_cached_research_reaches_the_prompt(client, db_session, capture_gen):
    job = client.post("/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": JD}).json()
    db_session.add(CompanyResearch(job_id=job["id"], findings="## Acme\nBuilds rockets.", sources=[]))
    db_session.commit()
    answers_service.draft_answer(db_session, "Why Acme?", job_id=job["id"])
    assert "Builds rockets" in capture_gen["user_content"]
    assert "Company research" in capture_gen["user_content"]


def test_no_research_row_means_no_research_block_and_no_search(client, db_session, capture_gen, monkeypatch):
    from app.services import research as research_service

    def no_search(*a, **k):
        raise AssertionError("draft_answer must never trigger a live search")

    monkeypatch.setattr(research_service.engine, "generate_search", no_search)
    job = client.post("/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": JD}).json()
    answers_service.draft_answer(db_session, "Why Acme?", job_id=job["id"])
    assert "Company research" not in capture_gen["user_content"]


def _stub_happy_path(monkeypatch):
    from app.services import apply as ap
    from app.services import tailor_flow

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
    monkeypatch.setattr(ap, "draft_cover_letter", lambda db_, job_, ctx: "Dear Acme, a letter.")
    monkeypatch.setattr(ap, "compile_tex", lambda tex, out, name: out / f"{name}.pdf")


def test_pipeline_runs_research_and_survives_its_failure(db_session, monkeypatch):
    db_session.add(Resume(name="SWE", job_type="software", tex_source="\\documentclass{article}", page_count=1))
    db_session.commit()
    _stub_happy_path(monkeypatch)
    calls = []

    def fake_research(db_, job_):
        calls.append(job_.company)
        raise RuntimeError("search down")  # failure must not block the pipeline

    monkeypatch.setattr(
        apply_service, "research_service", SimpleNamespace(research_company=fake_research)
    )
    s = apply_service.create_session(db_session, "https://jobs.example/apply", JD, fields=[], buttons=[])
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert calls == ["Acme"]
    assert s.stage == "ready" and s.status == "running"


def test_pipeline_skips_research_when_answers_disabled(db_session, monkeypatch):
    db_session.add(Resume(name="SWE", job_type="software", tex_source="\\documentclass{article}", page_count=1))
    db_session.commit()
    _stub_happy_path(monkeypatch)
    calls = []
    monkeypatch.setattr(
        apply_service,
        "research_service",
        SimpleNamespace(research_company=lambda db_, job_: calls.append(1)),
    )
    s = apply_service.create_session(
        db_session, "https://jobs.example/apply", JD, fields=[], buttons=[],
        options={"answer_questions": False},
    )
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    assert calls == []
    assert db_session.get(ApplySession, s.id).stage == "ready"
