"""GitHub repo sync -> project hubs, deterministic language->skill links."""

import pytest

from app.db.models import MemoryEntry, ProfileField
from app.services import github_sync
from app.services import memory as memory_service

REPOS = [
    {"name": "trackbot", "full_name": "jared/trackbot", "fork": False,
     "description": "Robot line follower", "language": "Python",
     "topics": ["robotics", "opencv"]},
    {"name": "dotfiles", "full_name": "jared/dotfiles", "fork": False,
     "description": None, "language": None, "topics": []},
    {"name": "forked-lib", "full_name": "jared/forked-lib", "fork": True,
     "description": "someone else's", "language": "C", "topics": []},
]


def _set_github(db, url="https://github.com/jared"):
    db.add(ProfileField(key="github", value=url))
    db.commit()


@pytest.mark.parametrize(
    "raw",
    [
        "https://github.com/jared",
        "github.com/jared/",
        "jared",
        "@jared",
        "www.github.com/jared",
    ],
)
def test_username_parses_accepted_forms(raw):
    assert github_sync._username({"github": raw}) == "jared"


def test_username_rejects_repo_url():
    """A full repo URL must not silently parse to its last path segment as the
    username -- it should fail with the friendly "couldn't read" error instead
    of treating "trackbot" as a username."""
    with pytest.raises(ValueError, match="Couldn't read"):
        github_sync._username({"github": "github.com/jared/trackbot"})


def test_sync_creates_project_hubs_and_skill_links(db_session, fake_embeddings, monkeypatch):
    _set_github(db_session)
    monkeypatch.setattr(github_sync, "_fetch_repos", lambda username: REPOS)
    result = github_sync.sync(db_session)
    assert result["username"] == "jared"
    assert result["repos_synced"] == 2          # fork skipped
    trackbot = db_session.query(MemoryEntry).filter_by(
        source="github:jared/trackbot").one()
    assert trackbot.type == "project" and "Robot line follower" in trackbot.content
    linked = memory_service.linked_entries(db_session, trackbot.id)
    assert any(other.title == "Python" and other.type == "skill" for _, other in linked)


def test_sync_is_idempotent(db_session, fake_embeddings, monkeypatch):
    _set_github(db_session)
    monkeypatch.setattr(github_sync, "_fetch_repos", lambda username: REPOS)
    github_sync.sync(db_session)
    again = github_sync.sync(db_session)
    assert again["hubs_created"] == 0           # nothing duplicated
    count = db_session.query(MemoryEntry).filter_by(
        source="github:jared/trackbot").count()
    assert count == 1


def test_sync_without_github_key_raises(db_session):
    with pytest.raises(ValueError, match="github"):
        github_sync.sync(db_session)


def test_sync_endpoint_maps_user_errors_to_422(client):
    resp = client.post("/api/profile/github/sync")
    assert resp.status_code == 422
    assert "github" in resp.json()["detail"].lower()
