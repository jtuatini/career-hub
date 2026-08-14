"""Cover-letter library: docs list filters/search + delete."""

import pytest

from app.db.models import ApplySession, GeneratedDoc


@pytest.fixture
def corpus(client, db_session):
    j1 = client.post("/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "x"}).json()
    j2 = client.post("/api/jobs", json={"company": "Globex", "title": "Quant Intern", "jd_text": "x"}).json()
    draft = GeneratedDoc(job_id=j1["id"], doc_type="cover_letter", tex_source="", body_text="draft about robotics")
    unvetted = GeneratedDoc(job_id=j1["id"], doc_type="cover_letter", tex_source="", body_text="pipeline letter", approved=True)
    vetted = GeneratedDoc(job_id=j2["id"], doc_type="cover_letter", tex_source="", body_text="edited quant letter", approved=True, vetted=True)
    resume = GeneratedDoc(job_id=j1["id"], doc_type="resume", tex_source="x")
    db_session.add_all([draft, unvetted, vetted, resume])
    db_session.commit()
    return {"draft": draft, "unvetted": unvetted, "vetted": vetted, "resume": resume}


def _ids(resp):
    return {d["id"] for d in resp.json()}


def test_doc_type_and_status_filters(client, corpus):
    all_cl = client.get("/api/docs?doc_type=cover_letter&limit=50")
    assert _ids(all_cl) == {corpus["draft"].id, corpus["unvetted"].id, corpus["vetted"].id}
    assert all(d["vetted"] in (True, False) for d in all_cl.json())

    assert _ids(client.get("/api/docs?doc_type=cover_letter&status=draft")) == {corpus["draft"].id}
    assert _ids(client.get("/api/docs?doc_type=cover_letter&status=unvetted")) == {corpus["unvetted"].id}
    assert _ids(client.get("/api/docs?doc_type=cover_letter&status=vetted")) == {corpus["vetted"].id}


def test_search_matches_body_company_title_case_insensitive(client, corpus):
    assert _ids(client.get("/api/docs?q=ROBOTICS")) == {corpus["draft"].id}
    assert corpus["vetted"].id in _ids(client.get("/api/docs?q=globex"))
    assert corpus["vetted"].id in _ids(client.get("/api/docs?q=quant"))


def test_delete_nulls_session_refs_and_unlinks_pdf(client, db_session, corpus, tmp_path):
    pdf = tmp_path / "cl.pdf"
    pdf.write_bytes(b"%PDF")
    corpus["vetted"].pdf_path = str(pdf)
    session = ApplySession(url="https://x", cover_doc_id=corpus["vetted"].id)
    db_session.add(session)
    db_session.commit()

    assert client.delete(f"/api/docs/{corpus['vetted'].id}").status_code == 204
    db_session.expunge(corpus["vetted"])
    assert db_session.get(GeneratedDoc, corpus["vetted"].id) is None
    db_session.refresh(session)
    assert session.cover_doc_id is None
    assert not pdf.exists()
    assert client.delete("/api/docs/999999").status_code == 404


def test_limit_is_bounded_and_validated(client, corpus):
    for bad in (0, 201, -1):
        assert client.get(f"/api/docs?limit={bad}").status_code == 422

    default = client.get("/api/docs")
    assert default.status_code == 200
    assert len(default.json()) <= 10

    assert client.get("/api/docs?limit=50").status_code == 200


def test_status_rejects_unknown_value(client, corpus):
    assert client.get("/api/docs?status=bogus").status_code == 422
    assert client.get("/api/docs?status=draft").status_code == 200


def test_deleted_letter_leaves_exemplar_pool(client, db_session, corpus, monkeypatch):
    from app.services import coverletter

    seen = {}

    def fake_gen(system, user_content, max_tokens=16000):
        seen["system"] = system
        return "body"

    monkeypatch.setattr(coverletter, "generate_text", fake_gen)
    job_id = corpus["vetted"].job_id
    client.delete(f"/api/docs/{corpus['vetted'].id}")
    client.post("/api/generate/cover-letter", json={"job_id": job_id})
    assert "edited quant letter" not in seen["system"]


def test_feed_items_carry_job_status_and_url(client, db_session):
    from app.db.models import GeneratedDoc, Job

    job = Job(company="Acme", title="SWE", status="applied", url="https://acme.jobs/1")
    db_session.add(job)
    db_session.commit()
    db_session.add(GeneratedDoc(job_id=job.id, doc_type="resume", tex_source="x"))
    db_session.commit()
    item = client.get("/api/docs?doc_type=resume&limit=1").json()[0]
    assert item["job_status"] == "applied"
    assert item["job_url"] == "https://acme.jobs/1"
