"""Worker configuration: one pydantic-settings model, single source of config."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from edrak.agents.customer_trends.schemas.common import SourceType

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
        validate_by_name=True,
        validate_by_alias=True,
    )

    # LLM (Ollama Cloud)
    ollama_base_url: str = "https://ollama.com"
    ollama_model: str = "gpt-oss:120b"
    ollama_api_key: SecretStr | None = None
    ollama_model_analysis: str | None = None
    ollama_model_relevance: str | None = None
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

    # Runtime (ENV and ARTIFACTS_PATH are shared with the platform; EDRAK_ENV also works)
    edrak_env: str = Field(default="development", validation_alias=AliasChoices("EDRAK_ENV", "ENV"))
    edrak_data_dir: Path = Path("./data")
    artifacts_path: Path = Path("artifacts")
    edrak_provider_mode: Literal["live", "fixture"] = "live"
    edrak_fixtures_dir: Path | None = None  # fixture mode reads here; default: the demo fixtures
    edrak_cache_ttl_s: int = 86400
    edrak_log_level: str = "INFO"
    edrak_fake_llm: bool = False
    branch_max_steps: int = Field(default=8, ge=1)  # model calls per collection branch
    branch_timeout_s: float = Field(default=100.0, gt=0)  # wall clock per collection branch
    # Relevance filter: drops comments that are noise before the analysis step reads them
    relevance_filter_enabled: bool = True
    relevance_item_types: Annotated[list[SourceType], NoDecode] = Field(
        default_factory=lambda: [SourceType.SOCIAL_COMMENT]
    )  # a list, or a comma separated string in the environment
    relevance_batch_size: int = Field(default=40, ge=1)  # comments per model call
    relevance_parent_chars: int = Field(default=300, ge=1)  # parent post text sent to the model
    relevance_comment_chars: int = Field(default=280, ge=1)  # comment text sent to the model
    relevance_concurrency: int = Field(default=2, ge=1)  # model calls in flight at once
    relevance_min_relevant: int = Field(default=5, ge=0)  # fewer relevant comments is a gap note
    relevance_timeout_s: float = Field(default=60.0, gt=0)  # wall clock for all model calls
    relevance_reasoning: str = "low"  # reasoning effort of the relevance model; empty leaves it off
    youtube_daily_quota: int = 10000
    youtube_search_daily_cap: int = 100

    # Optional tracing
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None

    @field_validator("relevance_item_types", mode="before")
    @classmethod
    def _split_item_types(cls, value: Any) -> Any:
        """The environment gives one string: `social_comment,review` or a JSON list."""
        if isinstance(value, str):
            text = value.strip()
            return (
                json.loads(text)
                if text.startswith("[")
                else [p.strip() for p in text.split(",") if p.strip()]
            )
        return value

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
