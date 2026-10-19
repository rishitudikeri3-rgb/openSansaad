"""Application settings, loaded from environment / .env.

Model names, API keys and service URLs live here and nowhere else.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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

    # Ingestion
    user_agent: str = "OpenSansad/0.1 (academic research project)"
    request_delay_seconds: float = 1.5
    data_dir: Path = Path("data")

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
