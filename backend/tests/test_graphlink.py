"""Auto-linking: engine-extracted entities become hubs + links, best-effort."""

import pytest

from app.services import graphlink
from app.services import memory as memory_service
from app.services.claude import ClaudeError


def _payload(entry_id, entities):
    return {"results": [{"entry_id": entry_id, "entities": entities}]}


def test_auto_link_creates_hubs_and_links(db_session, fake_embeddings, monkeypatch):
    entry = memory_service.create_entry(db_session, "story", "Regionals", "won with python code")
    monkeypatch.setattr(
        graphlink,
        "generate_json",
        lambda *a, **k: _payload(
            entry.id,
            [
                {"name": "Python", "kind": "skill", "relation": "demonstrates",
                 "description": "programming language"},
                {"name": "TrackBot", "kind": "project", "relation": "part_of",
                 "description": "robotics build"},
                {"name": "Nonsense", "kind": "planet", "relation": "related",
                 "description": "invalid kind, skipped"},
            ],
        ),
    )
    result = graphlink.auto_link(db_session, entry)
    assert result["hubs_created"] == 2 and result["links_created"] == 2
    titles = {other.title for _, other in memory_service.linked_entries(db_session, entry.id)}
    assert titles == {"Python", "TrackBot"}


def test_auto_link_reuses_existing_hub_case_insensitive(db_session, fake_embeddings, monkeypatch):
    hub = memory_service.create_entry(db_session, "skill", "Python", "language")
    entry = memory_service.create_entry(db_session, "story", "Hackathon", "used python")
    monkeypatch.setattr(
        graphlink,
        "generate_json",
        lambda *a, **k: _payload(
            entry.id,
            [{"name": "python", "kind": "skill", "relation": "used", "description": ""}],
        ),
    )
    result = graphlink.auto_link(db_session, entry)
    assert result["hubs_created"] == 0 and result["links_created"] == 1
    linked = memory_service.linked_entries(db_session, entry.id)
    assert linked[0][1].id == hub.id


def test_link_batch_engine_failure_is_nonfatal(db_session, fake_embeddings, monkeypatch):
    entry = memory_service.create_entry(db_session, "story", "S", "c")

    def boom(*a, **k):
        raise ClaudeError("engine down")

    monkeypatch.setattr(graphlink, "generate_json", boom)
    result = graphlink.link_batch(db_session, [entry])
    assert result["links_created"] == 0
    assert result["errors"] and "engine down" in result["errors"][0]


def test_link_batch_contains_unexpected_exceptions(db_session, fake_embeddings, monkeypatch):
    entry = memory_service.create_entry(db_session, "story", "S2", "c2")

    def boom(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(graphlink, "generate_json", boom)
    result = graphlink.link_batch(db_session, [entry])
    assert result["links_created"] == 0
    assert result["errors"] and "network down" in result["errors"][0]


def test_link_batch_excludes_muted_hubs(db_session, fake_embeddings, monkeypatch):
    muted_hub = memory_service.create_entry(db_session, "skill", "Python", "language")
    muted_hub.muted = True
    db_session.commit()
    entry = memory_service.create_entry(db_session, "story", "Hackathon", "used python")

    seen_user_content = []

    def fake(system, user_content, schema):
        seen_user_content.append(user_content)
        return _payload(
            entry.id,
            [{"name": "Python", "kind": "skill", "relation": "used", "description": "lang"}],
        )

    monkeypatch.setattr(graphlink, "generate_json", fake)
    result = graphlink.link_batch(db_session, [entry])

    assert "Python" not in seen_user_content[0]
    # Muted hub isn't reused: a fresh (unmuted) hub is created instead.
    assert result["hubs_created"] == 1 and result["links_created"] == 1
    titles = {other.title for _, other in memory_service.linked_entries(db_session, entry.id)}
    assert titles == {"Python"}
    new_hub = next(iter(memory_service.linked_entries(db_session, entry.id)))[1]
    assert new_hub.id != muted_hub.id


def test_link_batch_shape_invalid_entities_is_nonfatal(db_session, fake_embeddings, monkeypatch):
    entry = memory_service.create_entry(db_session, "story", "S3", "c3")
    monkeypatch.setattr(
        graphlink,
        "generate_json",
        lambda *a, **k: {"results": [{"entry_id": entry.id, "entities": ["not-a-dict"]}]},
    )
    result = graphlink.link_batch(db_session, [entry])
    assert result["links_created"] == 0
    assert result["errors"]


def test_link_batch_shape_invalid_results_is_nonfatal(db_session, fake_embeddings, monkeypatch):
    entry = memory_service.create_entry(db_session, "story", "S4", "c4")
    monkeypatch.setattr(graphlink, "generate_json", lambda *a, **k: {"results": "garbage"})
    result = graphlink.link_batch(db_session, [entry])
    assert result["links_created"] == 0
    assert result["errors"]


def test_create_endpoint_survives_shape_invalid_payload(client, fake_embeddings, monkeypatch):
    monkeypatch.setattr(graphlink, "generate_json", lambda *a, **k: {"results": "garbage"})
    resp = client.post("/api/memory", json={"type": "story", "title": "T2", "content": "c2"})
    assert resp.status_code == 201
    assert resp.json()["links"] == []


def test_organize_only_touches_unlinked_content(db_session, fake_embeddings, monkeypatch):
    linked = memory_service.create_entry(db_session, "story", "Linked", "already linked")
    hub = memory_service.create_entry(db_session, "skill", "Go", "language")
    memory_service.link_entries(db_session, linked.id, hub.id, "used")
    loose = memory_service.create_entry(db_session, "story", "Loose", "no links yet")

    seen: list[int] = []

    def fake(system, user_content, schema):
        assert "Loose" in user_content and "Linked" not in user_content
        seen.append(1)
        return _payload(loose.id, [])

    monkeypatch.setattr(graphlink, "generate_json", fake)
    result = graphlink.organize(db_session)
    assert seen and result["entries_processed"] == 1


def test_create_endpoint_survives_engine_failure(client, fake_embeddings, monkeypatch):
    def boom(*a, **k):
        raise ClaudeError("no engine")

    monkeypatch.setattr(graphlink, "generate_json", boom)
    resp = client.post("/api/memory", json={"type": "story", "title": "T", "content": "c"})
    assert resp.status_code == 201
    assert resp.json()["links"] == []
