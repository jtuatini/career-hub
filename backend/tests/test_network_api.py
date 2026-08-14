"""Network API: targets CRUD, people CRUD/filters, discover runner wiring."""

import threading
import time

import pytest

from app.db.models import Job, NetworkTarget, Person
from app.services.claude import ClaudeError


@pytest.fixture(autouse=True)
def _reset_network_status():
    """The discover runner's _status dict is module-level global state; reset
    it around every test in this file so real-worker tests (which mutate it)
    never leak into a sibling test's assertions."""
    from app.api import network as network_api

    network_api._status.update(running=False, done=0, total=0, last_error=None)
    yield
    network_api._status.update(running=False, done=0, total=0, last_error=None)


def test_targets_get_syncs_applied_jobs(client, db_session):
    db_session.add(Job(company="Acme", title="SWE", jd_text="x", status="applied"))
    db_session.commit()
    r = client.get("/api/network/targets")
    assert r.status_code == 200
    assert [(t["company"], t["source"]) for t in r.json()] == [("Acme", "application")]


def test_targets_manual_crud_and_derived_guard(client, db_session):
    r = client.post("/api/network/targets", json={"company": "SpaceY", "role_type": "GNC"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert client.patch(f"/api/network/targets/{tid}", json={"active": False}).status_code == 200
    assert client.delete(f"/api/network/targets/{tid}").status_code == 204

    db_session.add(NetworkTarget(company="Acme", source="application"))
    db_session.commit()
    derived = db_session.query(NetworkTarget).filter_by(source="application").one()
    assert client.delete(f"/api/network/targets/{derived.id}").status_code == 409


def test_people_filters_and_patch(client, db_session):
    db_session.add_all([
        Person(name="Sarah Chen", company="Acme", person_type="engineer", status="found",
               headline="GNC tooling"),
        Person(name="Raj Patel", company="Acme", person_type="recruiter", status="contacted"),
        Person(name="Ana Cruz", company="SpaceY", person_type="alumni", status="found"),
    ])
    db_session.commit()
    assert len(client.get("/api/network/people").json()) == 3
    assert len(client.get("/api/network/people?company=Acme").json()) == 2
    assert len(client.get("/api/network/people?person_type=recruiter").json()) == 1
    assert len(client.get("/api/network/people?status=found").json()) == 2
    assert len(client.get("/api/network/people?q=gnc").json()) == 1

    pid = client.get("/api/network/people?q=sarah").json()[0]["id"]
    r = client.patch(f"/api/network/people/{pid}", json={"status": "shortlisted", "notes": "warm"})
    assert r.status_code == 200 and r.json()["status"] == "shortlisted"
    assert client.patch(f"/api/network/people/{pid}", json={"status": "bogus"}).status_code == 422
    assert client.delete(f"/api/network/people/{pid}").status_code == 204


def test_create_target_is_idempotent_on_company_and_role_type(client, db_session):
    """Finding 3: re-adding the same manual target must not duplicate it —
    duplicate chips double discovery spend for the same company."""
    r1 = client.post("/api/network/targets", json={"company": "SpaceY", "role_type": "GNC"})
    assert r1.status_code == 201
    tid = r1.json()["id"]

    r2 = client.post("/api/network/targets", json={"company": "spacey", "role_type": "GNC"})
    assert r2.status_code == 200
    assert r2.json()["id"] == tid
    assert db_session.query(NetworkTarget).filter_by(source="manual").count() == 1

    r3 = client.post("/api/network/targets", json={"company": "SpaceY", "role_type": "Avionics"})
    assert r3.status_code == 201
    assert r3.json()["id"] != tid
    assert db_session.query(NetworkTarget).filter_by(source="manual").count() == 2


def test_people_capture_creates_and_schedules_enrich(client, monkeypatch):
    from app.api import network as network_api

    scheduled = []
    monkeypatch.setattr(network_api, "_run_enrich_async", lambda pid: scheduled.append(pid))
    r = client.post("/api/network/people", json={
        "name": "Lee Wong", "company": "Acme",
        "headline": "Flight Software", "profile_url": "https://linkedin.com/in/leew",
        "source": "linkedin_capture",
    })
    assert r.status_code == 201
    assert scheduled == [r.json()["id"]]


def test_people_create_source_is_restricted_to_enum(client, monkeypatch):
    """Finding 1: source is API-facing and must not accept arbitrary strings —
    only the discovery service is allowed to mint source="web_search" people,
    which requires an evidence URL. The API only accepts linkedin_capture and
    manual (default)."""
    from app.api import network as network_api

    monkeypatch.setattr(network_api, "_run_enrich_async", lambda pid: None)

    assert client.post("/api/network/people", json={
        "name": "Eve", "company": "Acme", "source": "web_search",
    }).status_code == 422
    assert client.post("/api/network/people", json={
        "name": "Eve", "company": "Acme", "source": "banana",
    }).status_code == 422

    r1 = client.post("/api/network/people", json={
        "name": "Eve", "company": "Acme", "source": "linkedin_capture",
    })
    assert r1.status_code == 201 and r1.json()["source"] == "linkedin_capture"

    r2 = client.post("/api/network/people", json={"name": "Eve2", "company": "Acme"})
    assert r2.status_code == 201 and r2.json()["source"] == "manual"


def test_discover_endpoint_starts_runner(client, db_session, monkeypatch):
    from app.api import network as network_api

    started = []
    monkeypatch.setattr(network_api, "_run_discover_async", lambda ids, force: started.append((ids, force)))
    db_session.add(NetworkTarget(company="Acme", source="manual"))
    db_session.commit()
    r = client.post("/api/network/discover", json={})
    assert r.status_code == 202 and r.json()["started"] == 1
    assert len(started) == 1

    s = client.get("/api/network/discover/status")
    assert set(s.json().keys()) == {"running", "done", "total", "last_error"}


def test_discover_worker_claude_error_on_one_target_still_processes_the_rest(db_session, monkeypatch):
    """Finding 3(a): a ClaudeError on target 1 of 2 is isolated — target 2 still
    runs, done reaches total, running ends False, last_error is set."""
    from app.api import network as network_api

    t1 = NetworkTarget(company="Acme", source="manual")
    t2 = NetworkTarget(company="SpaceY", source="manual")
    db_session.add_all([t1, t2])
    db_session.commit()
    ids = [t1.id, t2.id]

    calls = []

    def fake_discover(db, target, force=False):
        calls.append(target.company)
        if target.company == "Acme":
            raise ClaudeError("web search unavailable")
        return []

    monkeypatch.setattr(network_api.network_service, "discover", fake_discover)
    monkeypatch.setattr(network_api.network_service, "enrich", lambda db, p: p)

    # Finding 4: _run_discover_async now sets running/done/total/last_error
    # BEFORE starting the worker thread, so a direct call to the worker body
    # (as tests do to avoid racing a real thread) must seed that same
    # pre-start state itself instead of relying on the worker to do it.
    network_api._status.update(running=True, done=0, total=len(ids), last_error=None)
    network_api._discover_worker(ids, False)

    assert calls == ["Acme", "SpaceY"]
    assert network_api._status == {
        "running": False, "done": 2, "total": 2, "last_error": "web search unavailable",
    }


def test_discover_worker_survives_generic_commit_failure_mid_target(db_session, monkeypatch):
    """Finding 3(b) / Finding 1 regression pin: a generic exception from a DB
    operation (not ClaudeError) mid-target-1 must not leave the session needing
    a rollback for target 2's db.get() — pre-fix this raised
    sqlalchemy.exc.PendingRollbackError and silently dropped target 2."""
    from app.api import network as network_api

    t1 = NetworkTarget(company="Acme", source="manual")
    t2 = NetworkTarget(company="SpaceY", source="manual")
    db_session.add_all([t1, t2])
    db_session.commit()
    ids = [t1.id, t2.id]

    calls = []

    def fake_discover(db, target, force=False):
        calls.append(target.company)
        if target.company == "Acme":
            # A real generic (non-ClaudeError) failure during a commit inside
            # discover() — reproduced here with a NOT NULL violation, which
            # SQLAlchemy raises as IntegrityError and leaves the session's
            # transaction unusable until rolled back (same shape as a locked
            # SQLite raising mid-commit).
            db.add(NetworkTarget(company=None, source="manual"))
            db.commit()
        return []

    monkeypatch.setattr(network_api.network_service, "discover", fake_discover)
    monkeypatch.setattr(network_api.network_service, "enrich", lambda db, p: p)

    # See Finding 4 note in the previous test: seed the pre-start state that
    # _run_discover_async now sets before handing off to the worker thread.
    network_api._status.update(running=True, done=0, total=len(ids), last_error=None)
    network_api._discover_worker(ids, False)

    assert calls == ["Acme", "SpaceY"]
    assert network_api._status["done"] == 2
    assert network_api._status["total"] == 2
    assert network_api._status["running"] is False
    assert network_api._status["last_error"] is not None


def test_run_discover_async_sets_status_before_thread_starts(db_session, monkeypatch):
    """Finding 4: running/done/total/last_error must be visible synchronously
    to the caller of _run_discover_async, set BEFORE the worker thread starts
    — otherwise both the double-start guard and the frontend's first poll can
    race thread startup and observe stale running=False."""
    from app.api import network as network_api

    t = NetworkTarget(company="Acme", source="manual")
    db_session.add(t)
    db_session.commit()

    release = threading.Event()

    def blocked_discover(db, target, force=False):
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(network_api.network_service, "discover", blocked_discover)
    monkeypatch.setattr(network_api.network_service, "enrich", lambda db, p: p)

    network_api._run_discover_async([t.id], False)
    # No sleep before this assertion: it must hold immediately on return,
    # proving the status update happened in THIS thread, not the worker
    # (which is still blocked on release.wait()).
    assert network_api._status["running"] is True
    assert network_api._status["total"] == 1
    assert network_api._status["done"] == 0
    assert network_api._status["last_error"] is None

    release.set()
    for _ in range(200):  # let the worker finish before the test (and fixture teardown) returns
        if not network_api._status["running"]:
            break
        time.sleep(0.01)


def test_discover_endpoint_running_guard_is_idempotent_noop(client, monkeypatch):
    """Finding 3(c): a double-click while a run is already in flight must not
    spawn a second worker."""
    from app.api import network as network_api

    started = []
    monkeypatch.setattr(network_api, "_run_discover_async", lambda ids, force: started.append((ids, force)))
    network_api._status["running"] = True

    r = client.post("/api/network/discover", json={})
    assert r.status_code == 202
    assert r.json()["started"] == 0
    assert started == []
