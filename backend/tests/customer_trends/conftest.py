from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from edrak.agents.customer_trends.providers import http
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import RUN_ID, TASK_ID, synthetic_evidence


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test without worker variables from the shell or a developer .env, so no test
    can use a real key."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """A test that configures logging (the CLI does) must not leave its captured stream behind."""
    yield
    structlog.reset_defaults()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[EvidenceStore]:
    with EvidenceStore(tmp_path / "data" / "evidence.db") as opened:
        yield opened


@pytest.fixture
def evidence() -> list[EvidenceItem]:
    return synthetic_evidence()


@pytest.fixture
def loaded_store(store: EvidenceStore, evidence: list[EvidenceItem]) -> EvidenceStore:
    """A store holding one run with the 40 synthetic items in a single batch."""
    store.create_run(RUN_ID, TASK_ID)
    store.add_batch(RUN_ID, TASK_ID, "synthetic", evidence)
    return store


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP retries back off instantly in tests."""

    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(http, "_sleep", instant)
