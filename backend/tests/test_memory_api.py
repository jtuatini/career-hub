import pytest


@pytest.fixture
def brain(client, fake_embeddings):
    """Client plus three seeded memories and one QA answer."""
    entries = {}
    for type_, title, content in [
        ("story", "Robotics competition win", "Led embedded software for the robotics team"),
        ("skill", "Python backend work", "FastAPI SQLAlchemy services and testing"),
        ("personal", "Grew up in Wellington", "Moved for university, loves the outdoors"),
    ]:
        resp = client.post(
            "/api/memory", json={"type": type_, "title": title, "content": content}
        )
        assert resp.status_code == 201
        entries[type_] = resp.json()
    resp = client.post(
        "/api/qa",
        json={"question": "Why do you want to work here?", "answer": "Because of the mission."},
    )
    assert resp.status_code == 201
    entries["qa"] = resp.json()
    return entries


def test_create_rejects_unknown_type(client, fake_embeddings):
    resp = client.post(
        "/api/memory", json={"type": "dream", "title": "x", "content": "y"}
    )
    assert resp.status_code == 422
    assert "must be one of" in resp.json()["detail"]


def test_list_filters_by_type(client, brain):
    assert len(client.get("/api/memory").json()) == 3
    stories = client.get("/api/memory", params={"type": "story"}).json()
    assert [s["title"] for s in stories] == ["Robotics competition win"]


def test_search_ranks_by_meaning(client, brain):
    hits = client.post(
        "/api/memory/search", json={"query": "robotics embedded software", "k": 2}
    ).json()
    assert hits[0]["entry"]["title"] == "Robotics competition win"
    assert len(hits) == 2
    assert hits[0]["score"] > hits[1]["score"]


def test_search_type_filter(client, brain):
    hits = client.post(
        "/api/memory/search",
        json={"query": "robotics embedded software", "types": ["skill", "personal"]},
    ).json()
    assert all(h["entry"]["type"] in ("skill", "personal") for h in hits)


def test_update_reembeds(client, brain):
    entry_id = brain["personal"]["id"]
    resp = client.put(
        f"/api/memory/{entry_id}",
        json={"title": "Quantum navigation research", "content": "Kalman filters and IMUs"},
    )
    assert resp.status_code == 200
    hits = client.post(
        "/api/memory/search", json={"query": "quantum navigation kalman", "k": 1}
    ).json()
    assert hits[0]["entry"]["id"] == entry_id


def test_links_roundtrip_and_dedupe(client, brain):
    a, b = brain["story"]["id"], brain["skill"]["id"]
    resp = client.post(
        "/api/memory/links", json={"from_id": a, "to_id": b, "relation": "demonstrates"}
    )
    assert resp.status_code == 201
    first_id = resp.json()["id"]
    # Same edge again: updated, not duplicated.
    resp = client.post("/api/memory/links", json={"from_id": a, "to_id": b})
    assert resp.json()["id"] == first_id

    # Both endpoints see the link.
    assert client.get(f"/api/memory/{a}").json()["links"][0]["entry"]["id"] == b
    assert client.get(f"/api/memory/{b}").json()["links"][0]["entry"]["id"] == a


def test_link_validation(client, brain):
    a = brain["story"]["id"]
    assert client.post("/api/memory/links", json={"from_id": a, "to_id": a}).status_code == 422
    assert client.post("/api/memory/links", json={"from_id": a, "to_id": 999}).status_code == 422


def test_delete_removes_entry_and_links(client, brain):
    a, b = brain["story"]["id"], brain["skill"]["id"]
    client.post("/api/memory/links", json={"from_id": a, "to_id": b})
    assert client.delete(f"/api/memory/{a}").status_code == 204
    assert client.get(f"/api/memory/{a}").status_code == 404
    assert client.get(f"/api/memory/{b}").json()["links"] == []


def test_qa_search_and_mark_used(client, brain):
    client.post(
        "/api/qa",
        json={"question": "Describe a technical challenge", "answer": "Debugged a race."},
    )
    hits = client.post(
        "/api/qa/search", json={"query": "why work here mission", "k": 1}
    ).json()
    assert hits[0]["qa"]["question"] == "Why do you want to work here?"

    used = client.post(f"/api/qa/{hits[0]['qa']['id']}/mark-used")
    assert used.json()["times_used"] == 1
    assert client.post("/api/qa/999/mark-used").status_code == 404


def test_entity_types_accepted(client):
    for type_ in ("project", "company", "trait"):
        resp = client.post(
            "/api/memory",
            json={"type": type_, "title": f"A {type_}", "content": "hub node"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["muted"] is False


def test_mute_toggle(client):
    entry = client.post(
        "/api/memory",
        json={"type": "story", "title": "Muteme", "content": "secret story"},
    ).json()
    resp = client.put(f"/api/memory/{entry['id']}", json={"muted": True})
    assert resp.status_code == 200
    assert resp.json()["muted"] is True
    # and it round-trips off again
    resp = client.put(f"/api/memory/{entry['id']}", json={"muted": False})
    assert resp.json()["muted"] is False


def test_link_relation_vocabulary(client):
    a = client.post("/api/memory", json={"type": "story", "title": "A", "content": "a"}).json()
    b = client.post("/api/memory", json={"type": "skill", "title": "B", "content": "b"}).json()
    bad = client.post(
        "/api/memory/links",
        json={"from_id": a["id"], "to_id": b["id"], "relation": "vibes_with"},
    )
    assert bad.status_code == 422
    good = client.post(
        "/api/memory/links",
        json={"from_id": a["id"], "to_id": b["id"], "relation": "demonstrates"},
    )
    assert good.status_code == 201
