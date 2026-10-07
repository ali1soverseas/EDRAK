from pathlib import Path

import pytest

from edrak.agents.customer_trends import settings as settings_module
from edrak.agents.customer_trends.settings import Settings, find_repo_root, get_settings


def make(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]  # kwargs are test overrides


def test_defaults() -> None:
    s = make()
    assert s.ollama_base_url == "https://ollama.com"
    assert s.ollama_model == "gpt-oss:120b"
    assert s.ollama_api_key is None
    assert s.ollama_model_analysis is None
    assert s.ollama_temperature == 0.2
    assert s.ollama_timeout_s == 120
    assert s.edrak_provider_mode == "live"
    assert s.edrak_cache_ttl_s == 86400
    assert s.edrak_fake_llm is False
    assert s.youtube_daily_quota == 10000
    assert s.youtube_search_daily_cap == 100
    assert s.socialcrawl_base_url == "https://www.socialcrawl.dev/v1"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "other-model")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.7")
    monkeypatch.setenv("EDRAK_FAKE_LLM", "true")
    monkeypatch.setenv("EDRAK_PROVIDER_MODE", "fixture")
    s = make()
    assert s.ollama_model == "other-model"
    assert s.ollama_temperature == 0.7
    assert s.edrak_fake_llm is True
    assert s.edrak_provider_mode == "fixture"


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "")
    monkeypatch.setenv("OLLAMA_API_KEY", "")
    monkeypatch.setenv("OLLAMA_MODEL_ANALYSIS", "")
    s = make()
    assert s.ollama_model == "gpt-oss:120b"
    assert s.ollama_model_analysis is None
    assert not s.has_key("OLLAMA_API_KEY")


def test_env_file_with_inline_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OLLAMA_API_KEY=abc123\n"
        "OLLAMA_MODEL_ANALYSIS=            # optional, falls back to OLLAMA_MODEL\n"
        "EDRAK_FAKE_LLM=false               # true: scripted fake model\n"
        "EDRAK_PROVIDER_MODE=fixture        # live | fixture\n"
        "LLM_PROVIDER=shared-key-this-worker-ignores\n",
        encoding="utf-8",
    )
    s = Settings(_env_file=env)
    assert s.has_key("ollama_api_key")
    assert s.ollama_model_analysis is None
    assert s.edrak_fake_llm is False
    assert s.edrak_provider_mode == "fixture"


def test_repo_root_is_found_by_marker(tmp_path: Path) -> None:
    assert (find_repo_root() / "AGENTS.md").is_file()
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested / "file.py") == tmp_path.resolve()


def test_relative_paths_resolve_against_documented_bases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_module, "find_repo_root", lambda start=None: tmp_path)
    s = make()
    assert s.data_dir == (tmp_path / "backend" / "data").resolve()
    assert s.data_dir.is_dir()
    assert s.artifacts_dir == (tmp_path / "artifacts").resolve()
    assert s.artifacts_dir.is_dir()


def test_absolute_paths_are_kept(tmp_path: Path) -> None:
    s = make(edrak_data_dir=tmp_path / "d", artifacts_path=tmp_path / "a")
    assert s.data_dir == tmp_path / "d"
    assert s.artifacts_dir == tmp_path / "a"


def test_redacted_hides_secret_values() -> None:
    s = make(ollama_api_key="s3cret-value", serper_api_key="another-secret")
    view = s.redacted()
    assert view["ollama_api_key"] == "set"
    assert view["serper_api_key"] == "set"
    assert view["apify_token"] == "missing"
    assert view["ollama_model"] == "gpt-oss:120b"
    assert "s3cret-value" not in str(view)
    assert "another-secret" not in str(view)


def test_has_key_ignores_non_secret_and_unknown_names() -> None:
    s = make(youtube_api_key="k")
    assert s.has_key("YOUTUBE_API_KEY")
    assert not s.has_key("ollama_model")
    assert not s.has_key("does_not_exist")


def test_is_production() -> None:
    assert make(edrak_env="production").is_production
    assert make(edrak_env="prod").is_production
    assert not make(edrak_env="development").is_production


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_the_environment_name_reads_the_shared_env_key_or_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).is_production is False
    monkeypatch.setenv("ENV", "production")
    assert Settings(_env_file=None).is_production is True
    monkeypatch.setenv("EDRAK_ENV", "development")
    assert Settings(_env_file=None).is_production is False, "EDRAK_ENV wins over ENV"
    monkeypatch.delenv("ENV")
    monkeypatch.delenv("EDRAK_ENV")
    assert Settings(_env_file=None, edrak_env="prod").is_production is True
