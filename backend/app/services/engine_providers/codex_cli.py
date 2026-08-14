"""OpenAI Codex CLI provider — subscription (ChatGPT plan) auth via the CLI's
own login. Text/JSON only; search and image fall through the chain. The child
env is stripped of OPENAI_API_KEY so a subscription run can never silently
bill the metered API (same invariant as claude_cli/ANTHROPIC_API_KEY)."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import settings
from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers.common import (
    RUN_TIMEOUT_SECONDS,
    generate_json_via_text,
    workdir,
)

NAME = "codex"
CAPABILITIES = {"text", "json"}


def available() -> bool:
    return shutil.which("codex") is not None


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)  # subscription auth only
    return env


def generate_text(system: str, user_content: str) -> str:
    codex = shutil.which("codex")
    if codex is None:
        raise ClaudeError("codex CLI not found — install it and run `codex login`")
    ws = workdir()
    fd, out_name = tempfile.mkstemp(suffix=".txt", dir=ws)
    os.close(fd)
    out_path = Path(out_name)
    argv = [codex, "exec", "--skip-git-repo-check", "-s", "read-only",
            "--output-last-message", str(out_path)]
    model = engine_prefs.get_model("codex") or settings.codex_model
    if model:
        argv += ["-m", model]
    argv.append(f"{system}\n\n{user_content}")
    try:
        proc = subprocess.run(
            argv, cwd=ws, env=_build_env(), capture_output=True, text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            raise ClaudeError(
                f"codex CLI exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
            )
        return out_path.read_text().strip()
    finally:
        out_path.unlink(missing_ok=True)


def generate_json(system: str, user_content: str, schema: dict) -> dict:
    return generate_json_via_text(generate_text, system, user_content, schema)
