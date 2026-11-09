"""Live tests call real services with the keys of a developer's `.env` or shell. The suite-wide
isolation that hides those keys from every other test is lifted here, for these tests only."""

import os
from collections.abc import Iterator

import pytest

from edrak.agents.customer_trends.settings import Settings, find_repo_root, get_settings

# Read before any fixture runs: the suite-wide fixture removes these from the environment.
_SHELL_ENV = dict(os.environ)


def live_settings() -> Settings:
    """The settings a developer would get: the repository `.env` and the shell's variables."""
    env_file = find_repo_root() / ".env"
    return Settings(_env_file=env_file if env_file.is_file() else None)


@pytest.fixture(autouse=True)
def real_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name, value in _SHELL_ENV.items():
        if name.lower() in Settings.model_fields:
            monkeypatch.setenv(name, value)
    env_file = find_repo_root() / ".env"
    monkeypatch.setitem(Settings.model_config, "env_file", env_file if env_file.is_file() else None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
