from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py lives at <repo>/backend/src/edrak/core/config.py, so the repo root is
# four levels up. Anchoring paths to it makes the CLI and any future RAG
# ingestion independent of the current working directory; relative paths silently
# resolve against the CWD otherwise.
REPO_ROOT = Path(__file__).resolve().parents[4]

_ENV_FILE = REPO_ROOT / ".env"


def _project_path(relative: str) -> str:
    return str(REPO_ROOT / relative)


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env.

    Field names map directly to environment variable names, so LLM_MODEL in
    .env resolves to llm_model here. There is deliberately no env_prefix.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    debug: bool = False

    llm_provider: str = "ollama"
    llm_base_url: str = "https://ollama.com/v1"
    llm_model: str = "gpt-oss:120b"
    llm_api_key: SecretStr
    llm_temperature: float = 1.0

    max_replans: int = 2
    max_task_attempts: int = 3

    vector_store_path: str = _project_path("data/vector_store")
    internal_data_path: str = _project_path("data/internal")
    artifacts_path: str = _project_path("artifacts")

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000


settings = Settings()