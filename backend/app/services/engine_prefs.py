"""Runtime-selectable engine provider + per-provider CLI model overrides,
persisted to data/engine.json — a file, not .env, because the UI writes it
while the server runs. Absent or unreadable file means the defaults:
provider = claude, model override = "" (use the .env default)."""

import json

from app.config import settings

VALID = ("claude", "codex", "antigravity", "custom")


def _path():
    return settings.data_dir / "engine.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Legacy shim: the Gemini CLI engine was replaced by Antigravity (agy);
    # engine.json written before the rename may still say "gemini".
    if data.get("ai_provider") == "gemini":
        data["ai_provider"] = "antigravity"
    models = data.get("models")
    if isinstance(models, dict) and "gemini" in models:
        models.setdefault("antigravity", models.pop("gemini"))
    return data


def _write(data: dict) -> None:
    _path().write_text(json.dumps(data))


def get_provider() -> str:
    name = _read().get("ai_provider", "claude")
    return name if name in VALID else "claude"


def set_provider(name: str) -> None:
    if name not in VALID:
        raise ValueError(f"unknown provider {name!r}")
    data = _read()
    data["ai_provider"] = name
    _write(data)


def get_model(provider: str) -> str:
    """CLI model override for a provider; "" = use the .env default."""
    if provider not in VALID:
        raise ValueError(f"unknown provider {provider!r}")
    models = _read().get("models")
    model = models.get(provider, "") if isinstance(models, dict) else ""
    return model if isinstance(model, str) else ""


def get_custom_command() -> str:
    """Command template for the "custom" engine ("" = not configured)."""
    cmd = _read().get("custom_command", "")
    return cmd if isinstance(cmd, str) else ""


def set_custom_command(command: str) -> None:
    data = _read()
    data["custom_command"] = command.strip()
    _write(data)


def set_model(provider: str, model: str) -> None:
    if provider not in VALID:
        raise ValueError(f"unknown provider {provider!r}")
    data = _read()
    models = data.get("models")
    if not isinstance(models, dict):
        models = {}
    models[provider] = model.strip()
    data["models"] = models
    _write(data)
