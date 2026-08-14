def _create(client, **overrides):
    payload = {
        "company": "Acme Corp",
        "title": "SWE Intern",
        "url": "https://boards.greenhouse.io/acme/jobs/123",
        "jd_text": "We are looking for a software engineering intern...",
    }
    payload.update(overrides)
    return client.post("/api/jobs", json=payload)


def test_create_job_defaults_to_saved_status(client):
    resp = _create(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["company"] == "Acme Corp"
    assert body["status"] == "saved"


def test_list_jobs_filters_by_status(client):
    _create(client, company="A")
    jid = _create(client, company="B").json()["id"]
    client.patch(f"/api/jobs/{jid}", json={"status": "applied"})
    resp = client.get("/api/jobs", params={"status": "applied"})
    companies = [j["company"] for j in resp.json()]
    assert companies == ["B"]


def test_patch_job_updates_status_and_sets_applied_at(client):
    jid = _create(client).json()["id"]
    resp = client.patch(f"/api/jobs/{jid}", json={"status": "applied"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "applied"
    assert body["applied_at"] is not None


def test_patch_rejects_invalid_status(client):
    jid = _create(client).json()["id"]
    resp = client.patch(f"/api/jobs/{jid}", json={"status": "ghosted-me"})
    assert resp.status_code == 422


def test_get_job_includes_linked_docs(client):
    jid = _create(client).json()["id"]
    resp = client.get(f"/api/jobs/{jid}")
    assert resp.status_code == 200
    assert resp.json()["docs"] == []


def test_delete_job(client):
    jid = _create(client).json()["id"]
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_patch_job_url(client):
    job_id = client.post("/api/jobs", json={"company": "Acme", "title": "SWE"}).json()["id"]
    r = client.patch(f"/api/jobs/{job_id}", json={"url": "https://acme.jobs/1"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://acme.jobs/1"
