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
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.files_dir.mkdir(parents=True, exist_ok=True)
