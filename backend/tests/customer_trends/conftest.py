from collections.abc import Iterator
from pathlib import Path

import pytest

from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import RUN_ID, TASK_ID, synthetic_evidence


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test without worker variables from the shell or a developer .env."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()


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
