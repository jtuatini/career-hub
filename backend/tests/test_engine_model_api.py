"""PUT /api/engine/model + models/model_defaults in the status payload."""


def test_status_includes_models_and_defaults(client):
    body = client.get("/api/engine/status").json()
    assert body["models"] == {"claude": "", "codex": "", "gemini": ""}
    assert set(body["model_defaults"]) == {"claude", "codex", "gemini"}


def test_put_model_round_trips_in_status(client):
    r = client.put("/api/engine/model", json={"provider": "claude", "model": "sonnet"})
    assert r.status_code == 200
    assert r.json()["models"]["claude"] == "sonnet"
    assert client.get("/api/engine/status").json()["models"]["claude"] == "sonnet"


def test_put_empty_model_clears_override(client):
    client.put("/api/engine/model", json={"provider": "claude", "model": "sonnet"})
    r = client.put("/api/engine/model", json={"provider": "claude", "model": ""})
    assert r.json()["models"]["claude"] == ""


def test_put_model_rejects_unknown_provider(client):
    r = client.put("/api/engine/model", json={"provider": "gpt6", "model": "x"})
    assert r.status_code == 422
