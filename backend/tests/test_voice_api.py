"""Voice API: samples CRUD, profile read/edit/rebuild."""


def _add_sample(client, kind="formal", title="Essay", text="A real essay I wrote, long enough to count."):
    return client.post("/api/voice/samples", json={"title": title, "kind": kind, "text": text})


def test_sample_crud(client):
    resp = _add_sample(client)
    assert resp.status_code == 201
    sid = resp.json()["id"]
    listed = client.get("/api/voice/samples").json()
    assert [s["id"] for s in listed] == [sid]
    assert client.delete(f"/api/voice/samples/{sid}").status_code == 204
    assert client.get("/api/voice/samples").json() == []


def test_sample_rejects_bad_kind_and_short_text(client):
    assert client.post("/api/voice/samples", json={"title": "x", "kind": "casual", "text": "y" * 50}).status_code == 422
    assert client.post("/api/voice/samples", json={"title": "x", "kind": "formal", "text": "hi"}).status_code == 422


def test_sample_upload_txt(client):
    resp = client.post(
        "/api/voice/samples/upload",
        data={"title": "Old essay", "kind": "formal"},
        files={"file": ("essay.txt", b"This is my essay text, definitely long enough to keep.", "text/plain")},
    )
    assert resp.status_code == 201
    assert resp.json()["source"] == "essay.txt"


def test_profile_unbuilt_then_rebuild(client, monkeypatch):
    empty = client.get("/api/voice/profile").json()
    assert empty["content"] is None and empty["sample_count"] == 0

    assert client.post("/api/voice/profile/rebuild").status_code == 422  # no samples

    _add_sample(client)
    from app.services import voice

    monkeypatch.setattr(voice, "generate_json", lambda *a, **k: {"profile": "## Tone\nDirect."})
    resp = client.post("/api/voice/profile/rebuild")
    assert resp.status_code == 200
    assert "Direct" in resp.json()["content"]


def test_profile_manual_edit_and_rule_delete(client):
    # manual PUT creates the profile row when none exists yet
    resp = client.put(
        "/api/voice/profile",
        json={"content": "## Tone\nEdited by hand.", "learned_rules": [{"date": "2026-07-28", "rule": "keep"}]},
    )
    assert resp.status_code == 200
    got = client.get("/api/voice/profile").json()
    assert "Edited by hand" in got["content"]
    assert [r["rule"] for r in got["learned_rules"]] == ["keep"]

    resp = client.put("/api/voice/profile", json={"learned_rules": []})
    assert resp.json()["learned_rules"] == []


def test_rebuild_engine_failure_is_503(client):
    _add_sample(client)
    # conftest autouse stub raises ClaudeError
    assert client.post("/api/voice/profile/rebuild").status_code == 503
