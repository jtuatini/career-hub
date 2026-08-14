"""AI engine facade: every generation call goes through here.

Preference order (settings.ai_engine):
- "auto" (default): the user's Claude subscription via headless `claude -p`
  when the CLI is installed; the metered API only as fallback.
- "subscription" / "api": force one path, no fallback.

The subscription runner strips ANTHROPIC_API_KEY from the child environment
(terminal.build_env), so a subscription run can never silently bill the API.

Web-search calls (company research, people discovery) go through
generate_search, which prefers the CLI's own WebSearch tool and falls back to
the API's server-side web_search only when the subscription path is
unavailable — so nothing here requires a working API key.

Phase 4 (multi-provider): a runtime-selectable CLI provider (engine_prefs) may
run ahead of claude in the chain, but claude is always the trailing fallback
within the CLI chain, and the API remains the final fallback below that — so
with no engine.json present (the default provider = claude), the chain is
just [claude_cli], identical to the pre-registry behavior.

The subscription runners live in engine_providers/*.py; this module is just
the dispatch facade plus the metered-API fallback paths.
"""

import json
import subprocess
import sys
from types import ModuleType

import anthropic

from app.config import settings
from app.services import claude as api_engine
from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers import antigravity_cli, claude_cli, codex_cli, custom_cli
from app.services.engine_providers.claude_cli import (  # noqa: F401 — test seams
    _exec_cli,
    _extract_urls,
    _run_cli,
    _run_cli_search,
)
from app.services.engine_providers.common import (  # noqa: F401 — re-exported, previously module constants here
    RUN_TIMEOUT_SECONDS,
    SEARCH_TIMEOUT_SECONDS,
)
from app.services.engine_providers.common import extract_json as _extract_json  # noqa: F401

_PROVIDER_SEAMS = ("_exec_cli", "_run_cli", "_run_cli_search", "_extract_urls")


class _EngineModule(ModuleType):
    """Provider internals call these as bare names resolved in claude_cli's own
    globals, so rebinding them on THIS module must forward to claude_cli too.
    Keeps test_engine.py's monkeypatch seams working no matter how _dispatch
    is written."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name in _PROVIDER_SEAMS:
            setattr(claude_cli, name, value)


sys.modules[__name__].__class__ = _EngineModule

# Provider registry: name -> module, each exposing NAME/CAPABILITIES/available/
# generate_text/generate_json (claude also search/image). See engine_providers/.
PROVIDERS = {
    "claude": claude_cli,
    "codex": codex_cli,
    "antigravity": antigravity_cli,
    "custom": custom_cli,
}

# Status for the UI badge: which engine/provider handled the most recent call.
last_used: str | None = None  # "subscription" | "api" (legacy UI key)
last_provider: str | None = None  # which CLI provider served the last call


def _chain(capability: str):
    """CLI providers to try, in order: the selected one (if capable), then
    claude. Default selection = claude ⇒ chain is [claude_cli] — identical to
    the pre-registry behavior."""
    chain = []
    pref = engine_prefs.get_provider()
    mod = PROVIDERS.get(pref)
    if mod is not None and mod is not claude_cli and capability in mod.CAPABILITIES:
        chain.append(mod)
    if capability in claude_cli.CAPABILITIES:
        chain.append(claude_cli)
    return chain


def subscription_available() -> bool:
    return claude_cli.available()


def status() -> dict:
    return {
        "engine_preference": settings.ai_engine,
        "ai_provider": engine_prefs.get_provider(),
        "providers": {name: mod.available() for name, mod in PROVIDERS.items()},
        "models": {name: engine_prefs.get_model(name) for name in PROVIDERS},
        "model_defaults": {
            "claude": settings.claude_model,
            "codex": settings.codex_model,
            "antigravity": settings.antigravity_model,
            "custom": "",
        },
        "custom_command": engine_prefs.get_custom_command(),
        "subscription_available": subscription_available(),
        "api_key_configured": bool(settings.anthropic_api_key),
        "last_used": last_used,
        "last_provider": last_provider,
    }


def _api_search(system: str, user_content: str, max_uses: int) -> tuple[str, list[str]]:
    """Metered fallback: the API's server-side web_search tool, with its
    structured citations as the source list."""
    try:
        response = api_engine.get_client().messages.create(
            model=settings.claude_model,
            max_tokens=4000,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Anthropic API error {e.status_code}: {e.message}") from e
    text = "".join(b.text for b in response.content if b.type == "text")
    sources: list[str] = []
    for block in response.content:
        for cite in getattr(block, "citations", None) or []:
            url = getattr(cite, "url", None)
            if url and url not in sources:
                sources.append(url)
    return text, sources


def _dispatch(capability: str, cli_call, api_call):
    """Run per engine preference, recording which engine/provider served the
    call. Preserves ai_engine semantics: "api" = API only; "subscription" =
    CLI chain only, no API fallback; "auto" = chain first (skipping
    unavailable providers), API fallback.

    cli_call(provider_module) -> result.

    First-run case (auto mode, nothing in the chain available): this is NOT
    treated as a CLI failure — nothing was actually attempted, so we fall
    through to api_call() exactly like the pre-registry dispatcher did.
    api_call() raises its own actionable "ANTHROPIC_API_KEY is not set" error
    when there's no key, which is far more useful than a generic "no CLI
    provider" message on a friend's fresh install with nothing configured
    yet. The aggregated ClaudeError below is reserved for the case where at
    least one CLI provider was actually tried and failed (or mode ==
    "subscription", which always tries every provider in the chain).
    """
    global last_used, last_provider
    mode = settings.ai_engine
    if mode != "api":
        errors = []
        last_exc: Exception | None = None
        for mod in _chain(capability):
            if mode == "auto" and not mod.available():
                continue
            try:
                result = cli_call(mod)
                last_used = "subscription"
                last_provider = mod.NAME
                return result
            except (ClaudeError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as e:
                errors.append(f"{mod.NAME}: {e}")
                last_exc = e
        if errors and (mode == "subscription" or not settings.anthropic_api_key):
            raise ClaudeError(
                "Subscription engine failed ("
                + (
                    "; ".join(errors)
                    or "no CLI provider available (claude/codex/antigravity not installed or not logged in)"
                )
                + ") and no API fallback is available."
            ) from last_exc
    result = api_call()
    last_used = "api"
    last_provider = None
    return result


def generate_text(system: str, user_content: str, max_tokens: int = 16000) -> str:
    return _dispatch(
        "text",
        lambda mod: mod.generate_text(system, user_content),
        lambda: api_engine.generate_text(system, user_content, max_tokens),
    )


def generate_json(system: str, user_content: str, schema: dict, max_tokens: int = 16000) -> dict:
    return _dispatch(
        "json",
        lambda mod: mod.generate_json(system, user_content, schema),
        lambda: api_engine.generate_json(system, user_content, schema, max_tokens),
    )


def generate_search(system: str, user_content: str, max_uses: int = 8) -> tuple[str, list[str]]:
    """Web-grounded generation, subscription-first. Returns (text, source URLs)."""
    return _dispatch(
        "search",
        lambda mod: mod.search(system, user_content),
        lambda: _api_search(system, user_content, max_uses),
    )


def generate_json_with_image(system: str, png_bytes: bytes, schema: dict) -> dict:
    return _dispatch(
        "image",
        lambda mod: mod.image(system, png_bytes, schema),
        lambda: api_engine.generate_json_with_image(system, png_bytes, schema),
    )
