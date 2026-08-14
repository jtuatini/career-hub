"""User-defined CLI engine — any local command the user configures, so the
app can ride engines beyond the built-in three (e.g. `ollama run {model}`,
`llm -m {model}`, `aichat`). Text/JSON only; JSON goes through the
prompt-embedded-schema path since a generic CLI has no schema flag.

Command template rules: `{model}` is replaced by the model override from the
engine picker; `{prompt}` by the full prompt — when `{prompt}` is absent the
prompt is piped to stdin instead. The command is the user's own explicit
config, split with shlex and exec'd directly (never through a shell), with
metered API keys stripped from the child env."""

import os
import re
import shlex
import shutil
import subprocess

from app.services import engine_prefs
from app.services.claude import ClaudeError
from app.services.engine_providers.common import (
    RUN_TIMEOUT_SECONDS,
    generate_json_via_text,
    workdir,
)

NAME = "custom"
CAPABILITIES = {"text", "json"}

_STRIP_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")

# TTY spinners bleed ANSI/cursor codes into captured stdout (ollama does);
# strip them so downstream text/JSON parsing sees clean output.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|[\r\x07]")


def _argv_template() -> list[str]:
    cmd = engine_prefs.get_custom_command()
    if not cmd:
        return []
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return []
    # Convenience: a bare `ollama` means `ollama run {model}` — the one CLI
    # people reach for first shouldn't require knowing the template syntax.
    if argv == ["ollama"]:
        argv = ["ollama", "run", "{model}"]
    return argv


def available() -> bool:
    argv = _argv_template()
    return bool(argv) and shutil.which(argv[0]) is not None


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _STRIP_KEYS:
        env.pop(key, None)
    return env


def generate_text(system: str, user_content: str) -> str:
    argv_tpl = _argv_template()
    if not argv_tpl:
        raise ClaudeError(
            "custom engine command not set — open the engine picker and enter one "
            "(e.g. `ollama run {model}`)"
        )
    if shutil.which(argv_tpl[0]) is None:
        raise ClaudeError(f"custom engine binary {argv_tpl[0]!r} not found on PATH")
    prompt = f"{system}\n\n{user_content}"
    model = engine_prefs.get_model("custom")
    prompt_in_argv = False
    argv: list[str] = []
    for token in argv_tpl:
        if "{model}" in token:
            token = token.replace("{model}", model)
            if not token:  # bare {model} placeholder with no model set
                continue
        if "{prompt}" in token:
            token = token.replace("{prompt}", prompt)
            prompt_in_argv = True
        argv.append(token)
    proc = subprocess.run(
        argv,
        cwd=workdir(),
        env=_build_env(),
        input=None if prompt_in_argv else prompt,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise ClaudeError(
            f"custom engine exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}"
        )
    return _ANSI_RE.sub("", proc.stdout).strip()


def generate_json(system: str, user_content: str, schema: dict) -> dict:
    return generate_json_via_text(generate_text, system, user_content, schema)
