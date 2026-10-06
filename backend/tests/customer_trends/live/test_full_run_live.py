"""One full low-depth run of the GitLab pilot brief against the real model and providers, under a
strict budget, checked with the structural checks."""

from pathlib import Path

import pytest
from evals.customer_trends.checks import run_checks

from edrak.agents.customer_trends.runner import run_task
from edrak.agents.customer_trends.schemas.common import Budget, Depth
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from tests.customer_trends.factories import load_brief
from tests.customer_trends.live.conftest import live_settings

SETTINGS = live_settings()
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not SETTINGS.has_key("ollama_api_key"), reason="OLLAMA_API_KEY is not set"),
    pytest.mark.skipif(
        not (SETTINGS.has_key("apify_token") or SETTINGS.has_key("socialcrawl_api_key")),
        reason="no social data provider key (APIFY_TOKEN or SOCIALCRAWL_API_KEY)",
    ),
]


async def test_the_pilot_brief_runs_within_budget_and_passes_the_checks(tmp_path: Path) -> None:
    brief = load_brief("competitive_intelligence").model_copy(
        update={
            "depth": Depth.LIGHT,
            "run_id": "run-live-gitlab",
            "budget": Budget(max_tool_calls=20, max_cost_usd=0.50, max_seconds=180),
        }
    )
    settings = live_settings().model_copy(
        update={
            "edrak_data_dir": tmp_path / "data",
            "artifacts_path": tmp_path / "artifacts",
            "edrak_provider_mode": "live",
            "edrak_fake_llm": False,
        }
    )
    result = await run_task(brief, settings=settings)

    summary = result.control_summary
    assert summary.budget_used["tool_calls"] <= 20
    assert summary.budget_used["cost_usd"] <= 0.50
    assert summary.budget_used["seconds"] <= 190
    with EvidenceStore.from_settings(settings) as store:
        failures = run_checks(result, store, checkpoints=settings.data_dir / "checkpoints.db")
    assert failures == [], "\n".join(str(failure) for failure in failures)
