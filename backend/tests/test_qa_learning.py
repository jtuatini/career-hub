"""Q&A voice: profile injection into drafting, edit-learning on save."""

from app.db.models import StyleProfile
from app.services import answers, voice

DRAFT = "I am passionate about software engineering and eager to learn new things every day."
FINAL = "I got into software by breaking my robotics code the night before a meet — and fixing it."


def test_draft_uses_generic_voice_without_profile(client, fake_embeddings, monkeypatch):
    seen = {}

    def fake_gen(system, user_content, max_tokens=16000):
        seen["system"] = system
        return "draft"

    monkeypatch.setattr(answers, "generate_text", fake_gen)
    client.post("/api/qa/draft", json={"question": "Why us?"})
    assert "plain, direct voice" in seen["system"]


def test_draft_injects_profile_when_built(client, fake_embeddings, monkeypatch, db_session):
    db_session.add(StyleProfile(content="## Tone\nDry, direct."))
    db_session.commit()
    seen = {}

    def fake_gen(system, user_content, max_tokens=16000):
        seen["system"] = system
        return "draft"

    monkeypatch.setattr(answers, "generate_text", fake_gen)
    client.post("/api/qa/draft", json={"question": "Why us?"})
    assert "Dry, direct." in seen["system"]
    assert "plain, direct voice" not in seen["system"]


def test_save_with_edited_draft_learns(client, fake_embeddings, monkeypatch, db_session):
    db_session.add(StyleProfile(content="## Tone\nDirect."))
    db_session.commit()
    learned = {}
    monkeypatch.setattr(
        voice, "learn_from_edit",
        lambda db, draft, final, context: learned.update(draft=draft, final=final) or {"added": 1},
    )
    resp = client.post("/api/qa", json={"question": "Why us?", "answer": FINAL, "draft": DRAFT})
    assert resp.status_code == 201
    assert learned == {"draft": DRAFT, "final": FINAL}


def test_save_without_draft_does_not_learn(client, fake_embeddings, monkeypatch):
    called = []
    monkeypatch.setattr(voice, "learn_from_edit", lambda *a, **k: called.append(1))
    resp = client.post("/api/qa", json={"question": "Why us?", "answer": FINAL})
    assert resp.status_code == 201 and not called
