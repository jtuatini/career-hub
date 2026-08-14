"""Graph-expanded retrieval: seeds -> 1 hop -> 2nd hop through entity hubs."""

from app.services import memory as memory_service


def _mk(db, type_, title, content):
    return memory_service.create_entry(db, type_, title, content)


def test_search_memory_excludes_muted(db_session, fake_embeddings):
    kept = _mk(db_session, "story", "python win", "python contest victory")
    muted = _mk(db_session, "story", "python fail", "python contest disaster")
    muted.muted = True
    db_session.commit()
    hits = memory_service.search_memory(db_session, "python contest", k=5)
    ids = [e.id for e, _ in hits]
    assert kept.id in ids and muted.id not in ids


def test_retrieve_context_expands_through_hubs(db_session, fake_embeddings):
    story = _mk(db_session, "story", "regionals win", "won the robotics regionals")
    hub = _mk(db_session, "skill", "Python", "programming language")
    cousin = _mk(db_session, "experience", "sensor internship", "wrote sensor drivers")
    unrelated = _mk(db_session, "personal", "favorite food", "I like ramen noodles")
    memory_service.link_entries(db_session, story.id, hub.id, "demonstrates")
    memory_service.link_entries(db_session, cousin.id, hub.id, "demonstrates")

    ctx = memory_service.retrieve_context(db_session, "robotics regionals", k_seeds=1)
    ids = [e.id for e in ctx.entries]
    assert ids[0] == story.id                  # seed first
    assert hub.id in ids                       # 1 hop
    assert cousin.id in ids                    # 2nd hop through the hub
    assert unrelated.id not in ids or len(ids) <= 4
    assert ctx.seeds[0][0].id == story.id
    assert "regionals win" in ctx.markdown
    assert "demonstrates" in ctx.markdown      # relations rendered inline


def test_retrieve_context_skips_muted_and_caps(db_session, fake_embeddings):
    seed = _mk(db_session, "story", "hackathon", "built an app at the hackathon")
    hub = _mk(db_session, "skill", "React", "frontend library")
    hidden = _mk(db_session, "story", "hackathon shame", "app crashed on stage")
    hidden.muted = True
    db_session.commit()
    memory_service.link_entries(db_session, seed.id, hub.id, "used")
    memory_service.link_entries(db_session, hidden.id, hub.id, "used")

    ctx = memory_service.retrieve_context(db_session, "hackathon app", k_seeds=1)
    ids = [e.id for e in ctx.entries]
    assert hidden.id not in ids
    assert "hackathon shame" not in ctx.markdown

    for i in range(40):
        extra = _mk(db_session, "experience", f"filler {i}", f"filler entry {i}")
        memory_service.link_entries(db_session, extra.id, hub.id, "related")
    ctx = memory_service.retrieve_context(db_session, "hackathon app", k_seeds=1, cap=10)
    assert len(ctx.entries) <= 10


def test_graph_endpoint_shape(client):
    a = client.post("/api/memory", json={"type": "story", "title": "A", "content": "a"}).json()
    b = client.post("/api/memory", json={"type": "skill", "title": "B", "content": "b"}).json()
    client.post(
        "/api/memory/links",
        json={"from_id": a["id"], "to_id": b["id"], "relation": "demonstrates"},
    )
    client.put(f"/api/memory/{a['id']}", json={"muted": True})

    graph = client.get("/api/memory/graph").json()
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes[a["id"]]["muted"] is True
    assert nodes[a["id"]]["degree"] == 1 and nodes[b["id"]]["degree"] == 1
    assert graph["links"][0]["relation"] == "demonstrates"
    assert {"from_id", "to_id", "id"} <= set(graph["links"][0])
