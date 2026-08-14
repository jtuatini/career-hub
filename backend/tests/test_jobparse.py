import base64

from app.services import jobparse


def test_parse_text_with_url_and_confidence_clamp(client, monkeypatch):
    captured = {}

    def fake_generate_json(system, user_content, schema, **kw):
        captured["user"] = user_content
        return {
            "company": "Acme",
            "title": "SWE Intern",
            "location": "Denver, CO",
            "jd_text": "Build things.",
            "confidence": 1.7,  # model over-confidence gets clamped
        }

    monkeypatch.setattr(jobparse, "generate_json", fake_generate_json)
    resp = client.post(
        "/api/jobs/parse",
        json={"text": "RAW PAGE TEXT " * 10, "url": "https://boards.example/acme/1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"] == "Acme"
    assert body["confidence"] == 1.0
    assert "boards.example" in captured["user"]
    assert "RAW PAGE TEXT" in captured["user"]


def test_parse_image_path(client, monkeypatch):
    captured = {}

    def fake_image(system, png_bytes, schema, **kw):
        captured["png"] = png_bytes
        return {"company": "A", "title": "B", "location": "", "jd_text": "C", "confidence": 0.9}

    monkeypatch.setattr(jobparse, "generate_json_with_image", fake_image)
    png = b"\x89PNG-fake-bytes"
    resp = client.post(
        "/api/jobs/parse", json={"image_b64": base64.b64encode(png).decode()}
    )
    assert resp.status_code == 200
    assert captured["png"] == png


def test_parse_validation(client):
    assert client.post("/api/jobs/parse", json={}).status_code == 422
    assert (
        client.post("/api/jobs/parse", json={"text": "x", "image_b64": "eA=="}).status_code == 422
    )
    assert client.post("/api/jobs/parse", json={"image_b64": "!!notb64!!"}).status_code == 422
