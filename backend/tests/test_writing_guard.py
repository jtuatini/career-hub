"""AI-tell guardrails: scrub, detection, letterhead template, fill-only mode."""

import pytest

from app.db.models import ApplySession, Job, ProfileField
from app.services import apply as apply_service
from app.services import coverletter, writing

# ---------------------------------------------------------------- writing.py


def test_scrub_replaces_em_dashes_with_commas():
    assert writing.scrub("Fast iteration — that is the goal") == "Fast iteration, that is the goal"
    assert writing.scrub("tight—loop") == "tight, loop"


def test_scrub_keeps_numeric_ranges():
    assert writing.scrub("From 2024–2025 I built things") == "From 2024-2025 I built things"


def test_scrub_spaced_hyphen_between_words():
    assert writing.scrub("shipped it fast - and it worked") == "shipped it fast, and it worked"
    # A real minus between numbers is untouched.
    assert writing.scrub("5 - 3 = 2") == "5 - 3 = 2"


def test_find_tells_banned_phrases_and_parallelism():
    tells = writing.find_tells(
        "I am excited to leverage my proven track record. It's not just code, it's craft."
    )
    joined = " ".join(tells)
    assert "i am excited" in joined
    assert "leverage" in joined
    assert "proven track record" in joined
    assert "negative parallelism" in joined


def test_find_tells_rule_of_three_needs_repetition():
    one = "I build fast, reliable, and scalable systems."
    assert not any("rule-of-three" in t for t in writing.find_tells(one))
    two = one + " My work is tested, documented, and shipped."
    assert any("rule-of-three" in t for t in writing.find_tells(two))


def test_find_tells_clean_text_passes():
    assert writing.find_tells(
        "I built the ingest pipeline at Acme. It processed 400k events a minute. "
        "I want to do the same for your ground-station software."
    ) == []


# ----------------------------------------------------- letterhead template


@pytest.fixture
def profile(db_session):
    for k, v in {
        "full_name": "Alex Sample",
        "email": "alex@example.com",
        "phone": "555-010-4477",
        "linkedin": "https://www.linkedin.com/in/alex-sample/",
        "github": "https://github.com/alexsample",
        "city": "Springfield",
        "state": "Illinois",
    }.items():
        db_session.add(ProfileField(key=k, value=v))
    db_session.commit()
    return {
        "full_name": "Alex Sample",
        "email": "alex@example.com",
        "phone": "555-010-4477",
        "linkedin": "https://www.linkedin.com/in/alex-sample/",
        "github": "https://github.com/alexsample",
        "city": "Springfield",
        "state": "Illinois",
    }


def test_letterhead_contains_personal_info(profile):
    tex = coverletter.build_cover_letter_tex("Acme & Sons", "Body text here.", profile)
    assert "Alex Sample" in tex
    assert "alex@example.com" in tex
    assert "555-010-4477" in tex
    assert "linkedin.com/in/alex-sample" in tex and "https://" not in tex
    assert "github.com/alexsample" in tex
    assert "Springfield, Illinois" in tex
    assert "Acme \\& Sons" in tex
    assert "Dear Hiring Team," in tex
    assert "Sincerely," in tex
    # Signed with the real name, not an empty \signature{}.
    assert tex.count("Alex Sample") >= 2


def test_letterhead_without_profile_degrades():
    tex = coverletter.build_cover_letter_tex("Acme", "Body.")
    assert "\\scshape" not in tex  # no letterhead block
    assert "Dear Hiring Team," in tex and "Sincerely," in tex


def test_letterhead_compiles_for_real(tmp_path, profile):
    from app.services.latex import compile_tex

    tex = coverletter.build_cover_letter_tex(
        "Smith & Sons", "I cut costs 30% at R_D & Co. Math: $x$.", profile
    )
    pdf = compile_tex(tex, tmp_path, "letterhead")
    assert pdf.exists()


def test_draft_revision_round_fires_on_tells(db_session, monkeypatch):
    calls = []

    def fake_generate(system, user_content, max_tokens=16000):
        calls.append(system)
        if len(calls) == 1:
            return "I am excited to leverage my proven track record — truly."
        return "I built the thing. I want to build yours."

    monkeypatch.setattr(coverletter, "generate_text", fake_generate)
    monkeypatch.setattr(coverletter.voice_service, "voice_context", lambda db: "")
    monkeypatch.setattr(coverletter.voice_service, "critique_refine", lambda db, b, ctx: b)

    job = Job(company="Acme", title="SWE Intern", jd_text="Build backend services.")
    db_session.add(job)
    db_session.commit()

    body = coverletter.draft_cover_letter(db_session, job, "")
    assert len(calls) == 2  # draft + one revision round
    assert calls[1] == writing.REVISE_SYSTEM
    assert body == "I built the thing. I want to build yours."
    assert writing.find_tells(body) == []


def test_draft_skips_revision_when_clean(db_session, monkeypatch):
    calls = []

    def fake_generate(system, user_content, max_tokens=16000):
        calls.append(system)
        return "I built the ingest pipeline. It worked."

    monkeypatch.setattr(coverletter, "generate_text", fake_generate)
    monkeypatch.setattr(coverletter.voice_service, "voice_context", lambda db: "")
    monkeypatch.setattr(coverletter.voice_service, "critique_refine", lambda db, b, ctx: b)

    job = Job(company="Acme", title="SWE Intern", jd_text="Build backend services.")
    db_session.add(job)
    db_session.commit()

    coverletter.draft_cover_letter(db_session, job, "")
    assert len(calls) == 1


# ------------------------------------------------------------ fill-only mode


def test_fill_only_pipeline_skips_generation(db_session, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("engine must not be called in fill_only mode")

    monkeypatch.setattr(apply_service, "generate_json", boom)
    monkeypatch.setattr(apply_service.jobparse_service, "parse_posting", boom)
    monkeypatch.setattr(apply_service, "draft_cover_letter", boom)

    s = apply_service.create_session(
        db_session, "https://jobs.example/apply", "some page text", [], [], mode="fill_only"
    )
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.status == "running" and s.stage == "ready" and s.progress == 1.0
    assert s.job_id is None and s.resume_doc_id is None and s.cover_doc_id is None


def test_session_create_accepts_mode(client):
    r = client.post(
        "/api/apply/sessions",
        json={"url": "https://x", "page_text": "t", "mode": "fill_only"},
    )
    assert r.status_code == 201
    bad = client.post(
        "/api/apply/sessions",
        json={"url": "https://x", "page_text": "t", "mode": "bogus"},
    )
    assert bad.status_code == 422


# ------------------------------------------------------- per-feature switches


def _stub_parse_and_classify(monkeypatch):
    monkeypatch.setattr(
        apply_service.jobparse_service, "parse_posting",
        lambda text, url=None: {"company": "Acme", "title": "SWE Intern", "jd_text": "Build things.", "location": "", "confidence": 0.9},
    )
    monkeypatch.setattr(apply_service, "generate_json", lambda *a, **k: {"job_type": "software"})


def test_pipeline_option_skips_cover_letter(db_session, monkeypatch):
    from app.db.models import GeneratedDoc, Resume
    from app.services import tailor_flow

    _stub_parse_and_classify(monkeypatch)
    r = Resume(name="SWE", job_type="software", tex_source="\\documentclass{article}", page_count=1)
    db_session.add(r)
    db_session.commit()

    def fake_tailor(db_, resume_, job_, jd_):
        doc = GeneratedDoc(job_id=job_.id, base_resume_id=resume_.id, doc_type="resume", tex_source="t")
        db_.add(doc)
        db_.commit()
        return tailor_flow.TailorOutcome(doc=doc, pages=1)

    monkeypatch.setattr(apply_service.tailor_flow, "tailor_to_doc", fake_tailor)

    def boom(*a, **k):
        raise AssertionError("cover letter must not be drafted when switched off")

    monkeypatch.setattr(apply_service, "draft_cover_letter", boom)

    s = apply_service.create_session(
        db_session, "https://jobs.example/apply", "text", [], [],
        options={"tailor_resume": True, "cover_letter": False, "answer_questions": True},
    )
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.stage == "ready" and s.status == "running"
    assert s.resume_doc_id is not None and s.cover_doc_id is None


def test_pipeline_options_skip_all_generation(db_session, monkeypatch):
    _stub_parse_and_classify(monkeypatch)

    def boom(*a, **k):
        raise AssertionError("generation must not run")

    monkeypatch.setattr(apply_service.tailor_flow, "tailor_to_doc", boom)
    monkeypatch.setattr(apply_service, "draft_cover_letter", boom)

    s = apply_service.create_session(
        db_session, "https://jobs.example/apply", "text", [], [],
        options={"tailor_resume": False, "cover_letter": False},
    )
    apply_service.run_pipeline(s.id)
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.stage == "ready"
    assert s.job_id is not None  # posting still parsed for context
    assert s.resume_doc_id is None and s.cover_doc_id is None


def test_fillplan_answers_switch_skips_essays(db_session, monkeypatch):
    from app.services import fillplan

    def boom(*a, **k):
        raise AssertionError("essay drafting must not run when switched off")

    monkeypatch.setattr(fillplan.answers_service, "draft_answer", boom)

    s = apply_service.create_session(
        db_session, "https://x", "text", [], [],
        options={"answer_questions": False},
    )
    fields = [{"index": 0, "type": "textarea", "label": "Why do you want to work here?"}]
    actions = fillplan.build_plan(db_session, s, fields, [])
    assert not any(a.get("essay") for a in actions)


# --------------------------------------------------- posting-page apply click


def test_fillplan_clicks_apply_on_posting_page(db_session):
    from app.services import fillplan

    s = apply_service.create_session(db_session, "https://x", "posting text", [], [])
    buttons = [
        {"index": 0, "text": "Share"},
        {"index": 1, "text": "Apply now"},
    ]
    actions = fillplan.build_plan(db_session, s, [], buttons)
    assert actions == [
        {"kind": "click_start", "button_index": 1, "expect_text": "Apply now", "label": "Apply now"}
    ]


def test_fillplan_apply_click_needs_clean_session_and_no_fields(db_session):
    from app.services import fillplan

    # Prior fill results recorded -> a fieldless page could be a review step.
    s = apply_service.create_session(db_session, "https://x", "t", [], [])
    s.state = {**s.state, "results": [{"index": 0, "status": "filled"}]}
    db_session.commit()
    actions = fillplan.build_plan(db_session, s, [], [{"index": 0, "text": "Apply now"}])
    assert all(a["kind"] != "click_start" for a in actions)

    # End-of-flow verbs never qualify, even on a clean session.
    s2 = apply_service.create_session(db_session, "https://x", "t", [], [])
    actions = fillplan.build_plan(db_session, s2, [], [{"index": 0, "text": "Apply and submit"}])
    assert all(a["kind"] != "click_start" for a in actions)


def test_retry_updates_options(client, db_session):
    s = apply_service.create_session(
        db_session, "https://x", "t", [], [],
        options={"tailor_resume": True, "cover_letter": True, "answer_questions": True},
    )
    s.status = "error"
    db_session.commit()
    r = client.post(
        f"/api/apply/sessions/{s.id}/retry",
        json={"options": {"tailor_resume": True, "cover_letter": False, "answer_questions": True}},
    )
    assert r.status_code == 202
    db_session.expire_all()
    s = db_session.get(ApplySession, s.id)
    assert s.state["options"]["cover_letter"] is False
