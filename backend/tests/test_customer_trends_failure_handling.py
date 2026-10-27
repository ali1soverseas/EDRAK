import asyncio
from types import SimpleNamespace

from edrak.agents.customer_trends import nodes
from edrak.agents.customer_trends.providers.base import (
    ProviderExhausted,
    ProviderFailure,
)
from edrak.agents.customer_trends.schemas.common import UseCase
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.tools.base import execute_collection
from edrak.agents.customer_trends.tools.search_interest import SearchInterestInput
from pydantic import BaseModel


class _Input(BaseModel):
    query: str


def test_exhausted_provider_fallbacks_are_reported():
    failures = [
        ProviderFailure("primary", "Unavailable", "provider unavailable"),
        ProviderFailure("fallback", "QuotaExceeded", "quota exhausted"),
    ]

    class _Providers:
        async def call(self, *_args, **_kwargs):
            raise ProviderExhausted("social_search:x", failures)

    events = []
    context = SimpleNamespace(
        providers=_Providers(),
        branch="social",
        run_id="run",
        task_id="task",
        emit=events.append,
    )
    response = asyncio.run(
        execute_collection(
            context,
            "social_search",
            "social_search:x",
            _Input(query="NVIDIA"),
            {"query": "NVIDIA"},
        )
    )

    assert response.error_code == "provider_exhausted"
    assert response.fallback_used is True
    assert events[0]["fallback_used"] is True


def test_analyze_skips_metrics_when_collection_produced_no_data(monkeypatch):
    brief = TaskBrief(
        task_id="task",
        run_id="run",
        use_case=UseCase.PRODUCT_LAUNCH,
        entity="NVIDIA",
        question="Assess an inference platform launch",
    )
    state = {"brief": brief.model_dump(mode="json")}
    store = SimpleNamespace(
        run_summary=lambda _run_id: SimpleNamespace(evidence_count=0, batch_ids=[]),
        get_trend_series=lambda _run_id: [],
    )
    deps = SimpleNamespace(
        store=store,
        tool_context=lambda _brief: None,
        bus=SimpleNamespace(emit=lambda event: event),
        budget=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(model_dump=lambda **_kwargs: {})
        ),
    )
    calls = []

    async def _call_tool(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(nodes, "build_tools", lambda _context: {})
    monkeypatch.setattr(nodes, "call_tool", _call_tool)

    result = asyncio.run(nodes.analyze(state, deps))

    assert calls == []
    assert result["analysis_done"] is True
    assert any("metrics skipped" in warning for warning in result["warnings"])


def test_search_interest_timeout_is_bounded_by_collection_budget(monkeypatch):
    monkeypatch.setattr(nodes, "TREND_TIMEOUT_S", 75)

    assert nodes._search_interest_timeout(
        SimpleNamespace(budget=SimpleNamespace(collection_seconds_left=lambda: 30))
    ) == 30
    assert nodes._search_interest_timeout(
        SimpleNamespace(budget=SimpleNamespace(collection_seconds_left=lambda: 200))
    ) == 75
    assert nodes._search_interest_timeout(
        SimpleNamespace(budget=SimpleNamespace(collection_seconds_left=lambda: -5))
    ) == 0


def test_search_interest_accepts_common_granularity_words():
    base = {
        "run_id": "run",
        "task_id": "task",
        "keywords": ["NVIDIA"],
        "timeframe": "today 12-m",
    }

    assert SearchInterestInput(**base, granularity="monthly").granularity == "month"
    assert SearchInterestInput(**base, granularity="weekly").granularity == "week"
    assert SearchInterestInput(**base, granularity="daily").granularity == "day"
