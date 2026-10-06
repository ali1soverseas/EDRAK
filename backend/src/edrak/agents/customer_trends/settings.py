"""Worker configuration: one pydantic-settings model, single source of config."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_MARKER = "AGENTS.md"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: this file) to the directory holding AGENTS.md."""
    here = (start or Path(__file__)).resolve()
    for parent in (here, *here.parents):
        if (parent / _REPO_MARKER).is_file():
            return parent
    return Path.cwd()


def _resolve(path: Path, base: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else (base / expanded).resolve()


def _env_file() -> Path | None:
    candidate = find_repo_root() / ".env"
    return candidate if candidate.is_file() else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # LLM (Ollama Cloud)
    ollama_base_url: str = "https://ollama.com"
    ollama_model: str = "gpt-oss:120b"
    ollama_api_key: SecretStr | None = None
    ollama_model_analysis: str | None = None
    ollama_temperature: float = 0.2
    ollama_timeout_s: float = 120.0

    # Data providers
    apify_token: SecretStr | None = None
    apify_fallback_tokens: SecretStr | None = None  # comma separated
    socialcrawl_api_key: SecretStr | None = None
    socialcrawl_fallback_api_keys: SecretStr | None = None  # comma separated
    socialcrawl_base_url: str = "https://www.socialcrawl.dev/v1"
    youtube_api_key: SecretStr | None = None
    serper_api_key: SecretStr | None = None
    google_trends_api_key: SecretStr | None = None

    # Runtime (EDRAK_ENV and ARTIFACTS_PATH are shared with the rest of the platform)
    edrak_env: str = "development"
    edrak_data_dir: Path = Path("./data")
    artifacts_path: Path = Path("artifacts")
    edrak_provider_mode: Literal["live", "fixture"] = "live"
    edrak_cache_ttl_s: int = 86400
    edrak_log_level: str = "INFO"
    edrak_fake_llm: bool = False
    branch_max_steps: int = Field(default=8, ge=1)  # model calls per collection branch
    youtube_daily_quota: int = 10000
    youtube_search_daily_cap: int = 100

    # Optional tracing
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None

    @property
    def repo_root(self) -> Path:
        return find_repo_root()

    @property
    def is_production(self) -> bool:
        return self.edrak_env.lower() in {"prod", "production"}

    @property
    def data_dir(self) -> Path:
        """Relative paths resolve against `backend/`, so every entry point agrees."""
        path = _resolve(self.edrak_data_dir, self.repo_root / "backend")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def artifacts_dir(self) -> Path:
        """Relative paths resolve against the repository root, like the shared key."""
        path = _resolve(self.artifacts_path, self.repo_root)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def has_key(self, name: str) -> bool:
        value = getattr(self, name.lower(), None)
        return isinstance(value, SecretStr) and bool(value.get_secret_value())

    def key_list(self, primary: str, fallbacks: str) -> list[str]:
        """The primary key and then the comma separated fallbacks, without blanks or repeats."""
        values: list[str] = []
        for name in (primary, fallbacks):
            secret = getattr(self, name, None)
            if isinstance(secret, SecretStr):
                values.extend(part.strip() for part in secret.get_secret_value().split(","))
        return list(dict.fromkeys(value for value in values if value))

    def redacted(self) -> dict[str, str | int | float | bool | None]:
        """Settings safe to display: secrets become `set` or `missing`."""
        out: dict[str, str | int | float | bool | None] = {}
        for name, value in self:
            if isinstance(value, SecretStr) or (value is None and name in _SECRET_FIELDS):
                out[name] = "set" if self.has_key(name) else "missing"
            elif isinstance(value, Path):
                out[name] = str(value)
            else:
                out[name] = value
        return out


_SECRET_FIELDS = frozenset(
    name
    for name, field in Settings.model_fields.items()
    if SecretStr in getattr(field.annotation, "__args__", ())
)


@lru_cache
def get_settings() -> Settings:
    return Settings()
