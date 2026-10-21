"""Application settings, loaded from environment / .env.

Model names, API keys and service URLs live here and nowhere else.
"""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/opensansad/config.py -> repo root. Valid for the editable install `uv sync` creates.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Anchored to the repo root so notebooks/, workers etc. find .env regardless of cwd.
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Storage
    database_url: str = "postgresql+psycopg://opensansad:opensansad@localhost:5432/opensansad"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("opensansad-dev")
    redis_url: str = "redis://localhost:6379/0"

    # LLMs (Phase 2+), any OpenAI-compatible endpoint (Ollama, Groq, Gemini, OpenRouter, ...).
    # No defaults: must be set explicitly.
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    model_reasoning: str | None = None
    model_bulk: str | None = None

    # Ingestion. The cache refuses to fetch until the placeholder is replaced with a real contact.
    user_agent: str = "OpenSansad/0.1 (academic research project; contact: <your-email>)"
    request_delay_seconds: float = 1.5
    fetch_timeout_seconds: float = 30.0
    fetch_max_attempts: int = 4
    domain_block_hours: float = 6.0  # pause a whole domain after a 403
    domain_failure_threshold: int = 4  # consecutive connection failures before pausing
    domain_failure_block_hours: float = 1.0
    robots_ttl_days: float = 7.0  # robots.txt is re-fetched without force=True (as are listings)
    listing_ttl_hours: float = 24.0  # listing / API pages (bill lists, LOB) older than this
    url_lock_seconds: float = 900.0  # per-URL fetch lock expiry: > the longest single fetch
    url_lock_wait_seconds: float = 900.0  # give up waiting for another worker's fetch
    data_dir: Path = Path("data")

    @field_validator("data_dir")
    @classmethod
    def _anchor_data_dir(cls, v: Path) -> Path:
        """Relative paths resolve against the repo root, never the current directory."""
        return v if v.is_absolute() else PROJECT_ROOT / v

    @property
    def listing_max_age(self) -> timedelta:
        return timedelta(hours=self.listing_ttl_hours)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    @property
    def ground_truth_dir(self) -> Path:
        return self.data_dir / "ground_truth"


@lru_cache
def get_settings() -> Settings:
    return Settings()
