"""GitHub public-repo sync: repos become project hubs, languages become skill
links. The one sanctioned outbound call besides the Claude engine — public
data, user-triggered only, no token."""

import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MemoryEntry
from app.services import memory as memory_service
from app.services.autofill import load_profile

GITHUB_API = "https://api.github.com"
# Anchored at the start: the username must be the FIRST (and only) path segment
# after the host, so a repo URL like "github.com/jared/trackbot" fails to match
# instead of silently parsing "trackbot" as the username. A single optional
# trailing slash is allowed; anything past that (an extra path segment) breaks
# the match.
_USER_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:github\.com/)?@?"
    r"([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/?\s*$"
)


def _username(profile: dict[str, str]) -> str:
    raw = (profile.get("github") or "").strip()
    if not raw:
        raise ValueError("No github profile link set — add it in Profile → Links first")
    match = _USER_RE.search(raw)
    if match is None:
        raise ValueError(f"Couldn't read a GitHub username from {raw!r}")
    return match.group(1)


def _fetch_repos(username: str) -> list[dict]:
    resp = httpx.get(
        f"{GITHUB_API}/users/{username}/repos",
        params={"per_page": 100, "sort": "updated"},
        headers={"Accept": "application/vnd.github+json"},
        timeout=30,
    )
    if resp.status_code == 404:
        raise ValueError(f"GitHub user {username!r} not found")
    if resp.status_code == 403:
        raise ValueError("GitHub rate limit reached — try again in an hour")
    resp.raise_for_status()
    return resp.json()


def _repo_content(repo: dict) -> str:
    parts = [repo.get("description") or f"GitHub repository {repo['name']}."]
    if repo.get("language"):
        parts.append(f"Primary language: {repo['language']}.")
    if repo.get("topics"):
        parts.append("Topics: " + ", ".join(repo["topics"]) + ".")
    return " ".join(parts)


def sync(db: Session) -> dict:
    username = _username(load_profile(db))
    repos = _fetch_repos(username)
    result = {"username": username, "repos_synced": 0, "hubs_created": 0, "links_created": 0}
    for repo in repos:
        if repo.get("fork"):
            continue
        source = f"github:{repo['full_name']}"
        content = _repo_content(repo)
        entry = db.scalar(select(MemoryEntry).where(MemoryEntry.source == source))
        if entry is None:
            entry = memory_service.create_entry(
                db, "project", repo["name"], content,
                tags=(repo.get("topics") or [])[:5], source=source,
            )
            result["hubs_created"] += 1
        else:
            memory_service.update_entry(db, entry, title=repo["name"], content=content)
        result["repos_synced"] += 1

        language = repo.get("language")
        if language:
            skill = db.scalar(
                select(MemoryEntry).where(
                    MemoryEntry.type == "skill", MemoryEntry.title == language
                )
            )
            if skill is None:
                skill = memory_service.create_entry(
                    db, "skill", language, f"{language} (from GitHub repos)",
                    source="github-language",
                )
                result["hubs_created"] += 1
            existing = {other.id for _, other in memory_service.linked_entries(db, entry.id)}
            if skill.id not in existing:
                memory_service.link_entries(db, entry.id, skill.id, "used")
                result["links_created"] += 1
    return result
