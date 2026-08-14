from app.services.tailor import TailorResult, tailor_resume

MINIMAL_TEX = "\\documentclass{article}\\begin{document}hi\\end{document}"


def test_memory_context_lands_in_prompt():
    captured = {}

    def fake_generate(system, user_content, schema, **kw):
        captured["user"] = user_content
        return {"edits": []}

    tailor_resume("tex", "jd", generate=fake_generate, memory_context="- [story] Robotics: led team")
    assert "APPLICANT BACKGROUND" in captured["user"]
    assert "Robotics: led team" in captured["user"]

    tailor_resume("tex", "jd", generate=fake_generate)
    assert "APPLICANT BACKGROUND" not in captured["user"]


def test_endpoint_threads_brain_context(client, fake_embeddings, monkeypatch):
    from app.services import tailor_flow

    client.post("/api/memory", json={
        "type": "story",
        "title": "Robotics captain",
        "content": "Led autonomous navigation for the robotics team",
    })
    captured = {}

    def fake_tailor(tex, jd_text, **kwargs):
        captured.update(kwargs)
        return TailorResult(tex=MINIMAL_TEX)

    monkeypatch.setattr(tailor_flow, "tailor_resume", fake_tailor)
    resume = client.post("/api/resumes", json={
        "name": "Base", "job_type": "swe", "tex_source": MINIMAL_TEX,
    }).json()
    resp = client.post("/api/generate/tailor", json={
        "resume_id": resume["id"], "company": "Acme", "title": "SWE",
        "jd_text": "robotics navigation internship",
    })
    assert resp.status_code == 201
    assert "Robotics captain" in captured["memory_context"]
