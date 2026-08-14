"""Global token-bucket rate limits on /api: a hammering caller gets 429s,
normal UI traffic and health polling never do."""

from app import main as app_main


def test_burst_exhaustion_returns_429(client, monkeypatch):
    monkeypatch.setattr(app_main, "RATE_LIMIT", app_main.TokenBucket(rate=0, burst=3))
    for _ in range(3):
        assert client.get("/api/engine/status").status_code == 200
    resp = client.get("/api/engine/status")
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "2"


def test_bucket_refills_over_time(client, monkeypatch):
    bucket = app_main.TokenBucket(rate=1000, burst=1)
    monkeypatch.setattr(app_main, "RATE_LIMIT", bucket)
    assert client.get("/api/engine/status").status_code == 200
    bucket.updated -= 1  # simulate time passing instead of sleeping
    assert client.get("/api/engine/status").status_code == 200


def test_repeated_auth_failures_starved(client, monkeypatch):
    monkeypatch.setattr(app_main, "AUTH_FAIL_LIMIT", app_main.TokenBucket(rate=0, burst=2))
    bad = {"X-Copilot-Token": "wrong"}
    assert client.get("/api/jobs", headers=bad).status_code == 401
    assert client.get("/api/jobs", headers=bad).status_code == 401
    resp = client.get("/api/jobs", headers=bad)
    assert resp.status_code == 429
    # The good token is unaffected — only the failure budget is drained.
    assert client.get("/api/jobs").status_code == 200


def test_health_is_never_rate_limited(client, monkeypatch):
    monkeypatch.setattr(app_main, "RATE_LIMIT", app_main.TokenBucket(rate=0, burst=0))
    for _ in range(5):
        assert client.get("/api/health").status_code == 200
