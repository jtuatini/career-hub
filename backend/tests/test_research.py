from types import SimpleNamespace

import pytest

from app.services import research as research_service


@pytest.fixture
def stub_claude_client(monkeypatch):
    """Stub the engine's web-grounded search seam. research_company no longer
    calls the API directly — engine.generate_search picks the subscription CLI
    first and falls back to the metered API, and returns (text, sources)."""
    calls = {"n": 0}

    def fake_search(system, user_content, max_uses=6):
        calls["n"] += 1
        calls["system"] = system
        calls["user_content"] = user_content
        calls["max_uses"] = max_uses
        return (
            "## Acme\nBuilds rockets.",
            ["https://acme.example/about", "https://news.example/acme"],
        )

    monkeypatch.setattr(research_service.engine, "generate_search", fake_search)
    return calls


def test_research_runs_caches_and_forces(client, stub_claude_client):
    job = client.post(
        "/api/jobs", json={"company": "Acme", "title": "Propulsion Intern"}
    ).json()

    first = client.post(f"/api/jobs/{job['id']}/research")
    assert first.status_code == 200
    body = first.json()
    assert "rockets" in body["findings"].lower()
    assert body["sources"] == ["https://acme.example/about", "https://news.example/acme"]
    assert stub_claude_client["n"] == 1
    # Routed through the web-grounded search seam; only company + title sent.
    assert "Acme" in stub_claude_client["user_content"]
    assert stub_claude_client["user_content"] == "Research Acme for the role: Propulsion Intern."

    # Cached: no second engine call.
    assert client.post(f"/api/jobs/{job['id']}/research").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}/research").status_code == 200
    assert stub_claude_client["n"] == 1

    # force re-runs.
    client.post(f"/api/jobs/{job['id']}/research?force=true")
    assert stub_claude_client["n"] == 2


def test_research_404s(client, stub_claude_client):
    assert client.post("/api/jobs/999/research").status_code == 404
    job = client.post("/api/jobs", json={"company": "X", "title": "Y"}).json()
    assert client.get(f"/api/jobs/{job['id']}/research").status_code == 404


def test_prep_pack_grounds_in_brain(client, fake_embeddings, monkeypatch):
    from app.services import prep as prep_service

    client.post("/api/memory", json={
        "type": "story", "title": "Robotics captain", "content": "Led nav team",
    })
    job = client.post(
        "/api/jobs", json={"company": "Acme", "title": "SWE Intern", "jd_text": "robots"}
    ).json()
    captured = {}

    def fake_generate_json(system, user_content, schema, **kw):
        captured["user"] = user_content
        return {"questions": [{
            "question": "Tell me about leading a team",
            "why_asked": "leadership signal",
            "story_titles": ["Robotics captain", "Fabricated story"],
            "talking_points": ["led nav team"],
        }]}

    monkeypatch.setattr(prep_service, "generate_json", fake_generate_json)
    body = client.post(f"/api/jobs/{job['id']}/prep").json()
    assert "Robotics captain" in captured["user"]
    assert "robots" in captured["user"]
    # Hallucinated story titles are stripped.
    assert body["questions"][0]["story_titles"] == ["Robotics captain"]
    assert client.post("/api/jobs/999/prep").status_code == 404
