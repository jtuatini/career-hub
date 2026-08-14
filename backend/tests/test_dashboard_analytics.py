"""Dashboard extension of /api/analytics: action queue + section counts."""

from app.db.models import DocType, GeneratedDoc, Job, JobStatus, Person, Resume


def _mk(db, **kw):
    job = Job(company=kw.pop("company", "Acme"), title=kw.pop("title", "Intern"), **kw)
    db.add(job)
    db.commit()
    return job


def test_needs_resume_excludes_tailored_and_closed(client, db_session):
    j_open = _mk(db_session, company="NeedsWork")
    j_done = _mk(db_session, company="HasResume")
    db_session.add(GeneratedDoc(job_id=j_done.id, doc_type=DocType.RESUME, tex_source="x"))
    _mk(db_session, company="Gone", status=JobStatus.REJECTED)
    db_session.commit()
    q = client.get("/api/analytics").json()["action_queue"]
    companies = [row["company"] for row in q["needs_resume"]]
    assert "NeedsWork" in companies and "HasResume" not in companies and "Gone" not in companies
    assert q["needs_resume"][0].keys() >= {"job_id", "company", "title", "status"}


def test_prep_ready_is_oa_and_interview(client, db_session):
    _mk(db_session, company="OaCo", status=JobStatus.OA)
    _mk(db_session, company="IntCo", status=JobStatus.INTERVIEW)
    _mk(db_session, company="SavedCo")
    q = client.get("/api/analytics").json()["action_queue"]
    assert {row["company"] for row in q["prep_ready"]} == {"OaCo", "IntCo"}


def test_drafts_lists_unapproved_docs(client, db_session):
    j = _mk(db_session, company="DraftCo")
    db_session.add(GeneratedDoc(job_id=j.id, doc_type=DocType.COVER_LETTER, tex_source="x"))
    db_session.add(GeneratedDoc(job_id=j.id, doc_type=DocType.RESUME, tex_source="x", approved=True))
    db_session.commit()
    q = client.get("/api/analytics").json()["action_queue"]
    assert len(q["drafts"]) == 1
    assert q["drafts"][0]["doc_type"] == "cover_letter" and q["drafts"][0]["company"] == "DraftCo"


def test_counts(client, db_session):
    j = _mk(db_session, company="CountCo")
    db_session.add(GeneratedDoc(job_id=j.id, doc_type=DocType.RESUME, tex_source="x"))
    db_session.add(GeneratedDoc(job_id=j.id, doc_type=DocType.COVER_LETTER, tex_source="x"))
    db_session.add(Resume(name="base", job_type="software"))
    db_session.add(Person(name="P", company="CountCo", person_type="peer",
                          evidence_urls=[], match_signals=[], source="manual", status="found"))
    db_session.commit()
    c = client.get("/api/analytics").json()["counts"]
    assert c == {"tailored": 1, "letters": 1, "bank_resumes": 1, "jobs": 1, "network_people": 1}
