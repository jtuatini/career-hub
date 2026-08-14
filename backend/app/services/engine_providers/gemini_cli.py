"""Google Gemini CLI provider — Google-account auth via the CLI's own login.
Text/JSON only; search and image fall through the chain. The child env is
stripped of GEMINI_API_KEY/GOOGLE_API_KEY so a subscription/free-tier run can
never silently bill a metered key."""

import os
import shutil
import subprocess

from app.config import settings
from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers.common import (
    RUN_TIMEOUT_SECONDS,
    generate_json_via_text,
    workdir,
)

NAME = "gemini"
CAPABILITIES = {"text", "json"}


def available() -> bool:
    return shutil.which("gemini") is not None


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    return env


def generate_text(system: str, user_content: str) -> str:
    gemini = shutil.which("gemini")
    if gemini is None:
        raise ClaudeError("gemini CLI not found — install it and sign in once interactively")
    # The gemini CLI has no separate system-prompt flag in headless mode;
    # prepend it to the prompt.
    argv = [gemini, "-p", f"{system}\n\n{user_content}"]
    model = engine_prefs.get_model("gemini") or settings.gemini_model
    if model:
        argv += ["-m", model]
    proc = subprocess.run(
        argv, cwd=workdir(), env=_build_env(), capture_output=True, text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise ClaudeError(
            f"gemini CLI exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
        )
    return proc.stdout.strip()


def generate_json(system: str, user_content: str, schema: dict) -> dict:
    return generate_json_via_text(generate_text, system, user_content, schema)
