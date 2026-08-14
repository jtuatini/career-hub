import io

import pytest

from app.db.base import get_db
from app.db.models import GeneratedDoc, Job
from app.main import app
from app.services import answers as answers_service
from app.services import atscheck
from app.services import ingest as ingest_service
from app.services.latex import compile_tex


def _test_db():
    """Session on the client fixture's temp database."""
    return next(app.dependency_overrides[get_db]())


# ——— Ingestion ———


def test_ingest_creates_entries_and_skips_invalid(client, fake_embeddings, monkeypatch):
    captured = {}

    def fake_generate_json(system, user_content, schema, **kw):
        captured["user_content"] = user_content
        return {
            "entries": [
                {"type": "story", "title": "Hackathon win", "content": "Built X overnight",
                 "tags": ["hackathon"]},
                {"type": "dream", "title": "bad type", "content": "skipped", "tags": []},
                {"type": "skill", "title": "", "content": "no title", "tags": []},
            ]
        }

    monkeypatch.setattr(ingest_service, "generate_json", fake_generate_json)
    resp = client.post(
        "/api/memory/ingest",
        files={"file": ("old-essays.txt", io.BytesIO(b"I once built X overnight at a hackathon" * 3))},
    )
    assert resp.status_code == 201
    created = resp.json()
    assert [e["title"] for e in created] == ["Hackathon win"]
    assert created[0]["source"] == "old-essays.txt"
    assert "hackathon" in captured["user_content"]
    # The new entry is immediately retrievable.
    hits = client.post("/api/memory/search", json={"query": "hackathon win", "k": 1}).json()
    assert hits[0]["entry"]["title"] == "Hackathon win"


def test_ingest_rejects_empty_file(client, fake_embeddings):
    resp = client.post("/api/memory/ingest", files={"file": ("empty.txt", io.BytesIO(b"hi"))})
    assert resp.status_code == 422


def test_ingest_reads_pdf_and_maps_missing_key_to_503(client, fake_embeddings, monkeypatch, tmp_path):
    pdf = compile_tex(
        "\\documentclass{article}\\begin{document}"
        "Robotics captain: led autonomous navigation, sensor fusion, and pit-crew "
        "logistics for a twelve-person FIRST team across two seasons."
        "\\end{document}",
        tmp_path,
        "ingest_src",
    )
    captured = {}

    def failing_generate_json(system, user_content, schema, **kw):
        captured["user_content"] = user_content
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    monkeypatch.setattr(ingest_service, "generate_json", failing_generate_json)
    resp = client.post(
        "/api/memory/ingest", files={"file": ("resume.pdf", io.BytesIO(pdf.read_bytes()))}
    )
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]
    assert "Robotics" in captured["user_content"]  # PDF text extraction worked


# ——— Q&A drafting ———


@pytest.fixture
def qa_context(client, fake_embeddings):
    client.post("/api/memory", json={
        "type": "story",
        "title": "Robotics captain",
        "content": "Led the robotics team autonomous navigation effort",
    })
    client.post("/api/qa", json={
        "question": "Tell us about a leadership experience",
        "answer": "I led our robotics team through a rebuild.",
    })
    job = client.post(
        "/api/jobs", json={"company": "Acme", "title": "Controls Intern", "jd_text": "robots"}
    ).json()
    return job


def test_draft_uses_brain_and_past_answers(client, qa_context, monkeypatch):
    captured = {}

    def fake_generate_text(system, user_content, **kw):
        captured["system"] = system
        captured["user"] = user_content
        return "Drafted in voice."

    monkeypatch.setattr(answers_service, "generate_text", fake_generate_text)
    resp = client.post(
        "/api/qa/draft",
        json={"question": "Describe leading a robotics team", "job_id": qa_context["id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["draft"] == "Drafted in voice."
    assert body["memories_used"][0]["title"] == "Robotics captain"
    assert body["past_answers_used"][0]["question"] == "Tell us about a leadership experience"
    assert "I led our robotics team through a rebuild." in captured["system"]
    assert "autonomous navigation" in captured["user"]
    assert "Controls Intern at Acme" in captured["user"]


def test_draft_without_key_is_503(client, qa_context, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    monkeypatch.setattr(answers_service, "generate_text", boom)
    assert client.post("/api/qa/draft", json={"question": "Why us?"}).status_code == 503


# ——— ATS + keywords ———


def test_jd_keywords_filters_and_ranks():
    jd = "Python Python Python Kubernetes Kubernetes the and with strong ability GraphQL"
    terms = atscheck.jd_keywords(jd, limit=3)
    assert terms == ["python", "kubernetes", "graphql"]


def test_ats_report_on_real_pdf(tmp_path):
    pdf = compile_tex(
        "\\documentclass{article}\\begin{document}"
        "Python developer with Kubernetes and testing background."
        "\\end{document}",
        tmp_path,
        "ats_sample",
    )
    report = atscheck.ats_report(pdf, "Python Kubernetes Terraform Terraform")
    assert report["ats_readable"] is False  # tiny doc, honest signal
    assert "python" in report["present_keywords"]
    assert "kubernetes" in report["present_keywords"]
    assert "terraform" in report["missing_keywords"]
    assert 0 < report["keyword_score"] < 1


def test_ats_endpoint(client, tmp_path):
    pdf = compile_tex(
        "\\documentclass{article}\\begin{document}Python everywhere\\end{document}",
        tmp_path,
        "ats_doc",
    )
    db = _test_db()
    job = Job(company="Acme", title="SWE", jd_text="Python microservices")
    db.add(job)
    db.flush()
    doc = GeneratedDoc(job_id=job.id, doc_type="resume", tex_source="x", pdf_path=str(pdf))
    db.add(doc)
    db.commit()

    report = client.get(f"/api/docs/{doc.id}/ats").json()
    assert "python" in report["present_keywords"]
    assert "microservices" in report["missing_keywords"]
    assert client.get("/api/docs/999/ats").status_code == 404


def test_tailor_maps_claude_error_to_503(client, monkeypatch):
    from app.services import tailor_flow
    from app.services.claude import ClaudeError

    resume = client.post(
        "/api/resumes",
        json={
            "name": "Min",
            "job_type": "swe",
            "tex_source": "\\documentclass{article}\\begin{document}hi\\end{document}",
        },
    ).json()

    def boom(*a, **kw):
        raise ClaudeError("Anthropic rejected the API key (401)")

    monkeypatch.setattr(tailor_flow, "tailor_resume", boom)
    resp = client.post(
        "/api/generate/tailor",
        json={"resume_id": resume["id"], "company": "Acme", "title": "SWE", "jd_text": "python"},
    )
    assert resp.status_code == 503
    assert "401" in resp.json()["detail"]
