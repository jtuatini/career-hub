"""Shared-token auth for the local API.

A random token is generated once into data/api_token (mode 600). The Vite dev
proxy injects it server-side so the web app never exposes it to the browser;
the extension stores it after a one-time paste. This is defense against
browser-mediated callers (arbitrary websites, other extensions) — native local
processes run as the user and are out of scope.
"""

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from app.config import settings


def token_path() -> Path:
    return settings.data_dir / "api_token"


def get_token() -> str:
    path = token_path()
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def _sign(path: str, expires: int) -> str:
    return hmac.new(
        get_token().encode(), f"{path}:{expires}".encode(), hashlib.sha256
    ).hexdigest()


def ticket_query(path: str, ttl_seconds: int = 120) -> str:
    """Short-lived signed query for opening a protected URL in a plain browser
    tab (no header possible). The token itself never appears in the URL."""
    expires = int(time.time()) + ttl_seconds
    return f"exp={expires}&sig={_sign(path, expires)}"


def verify_ticket(path: str, exp: str | None, sig: str | None) -> bool:
    if not exp or not sig:
        return False
    try:
        expires = int(exp)
    except ValueError:
        return False
    if expires < time.time():
        return False
    return hmac.compare_digest(_sign(path, expires), sig)
