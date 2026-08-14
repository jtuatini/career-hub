import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / "backend" / ".env", extra="ignore")

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-4-8"
    # "auto" = subscription (claude CLI) first, API fallback; or "subscription"/"api".
    ai_engine: str = "auto"
    # Cap on how far a tailored resume may drift from its base (word-diff ratio).
    tailor_max_divergence: float = 0.25
    # Page-guard tighten attempts before tailoring hard-fails.
    tailor_max_tighten_rounds: int = 3
    # Overfull-vbox tolerance (pt): at/above this, a "1-page" resume that
    # overflows the bottom margin counts as a page-fit failure.
    tailor_max_overfull_pt: float = 2.0
    data_dir: Path = REPO_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8321
    # Multi-provider engines (Phase 4): CLI model overrides. Empty = CLI default.
    codex_model: str = ""
    gemini_model: str = ""
    # Optional local hiring-agent repo (github.com/interviewstreet/hiring-agent)
    # for the "hiring_agent" ATS scan kind. Empty = feature hidden in the UI.
    ats_repo_path: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "appbot.sqlite3"

    @property
    def files_dir(self) -> Path:
        """Uploaded resume sources and compiled PDFs."""
        return self.data_dir / "files"


settings = Settings()
# data/ holds everything personal (resumes, memory web, contacts): keep it out
# of reach of other users on a shared machine. The 0o077 umask covers files
# created from here on (DB + WAL/SHM sidecars, compiled PDFs) in every
# entrypoint that imports config (uvicorn, alembic, the MCP server); the chmods
# retrofit installs whose files predate it.
os.umask(0o077)
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.files_dir.mkdir(parents=True, exist_ok=True)
settings.data_dir.chmod(0o700)
settings.files_dir.chmod(0o700)
for _db_file in settings.data_dir.glob("appbot.sqlite3*"):
    _db_file.chmod(0o600)
