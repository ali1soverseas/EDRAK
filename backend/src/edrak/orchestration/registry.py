"""Default worker registry builder for EDRAK orchestration."""

from __future__ import annotations

import logging
from typing import Any

from edrak.contracts import WorkerRegistry, WorkerType

logger = logging.getLogger(__name__)


def get_default_registry() -> WorkerRegistry:
    """Build and return a WorkerRegistry with all available intelligence workers registered."""
    registry = WorkerRegistry()

    try:
        from edrak.agents.competitor_intelligence import CompetitorAgent

        registry.register(WorkerType.COMPETITOR_INTELLIGENCE, CompetitorAgent())
    except Exception as exc:
        logger.warning("Competitor intelligence worker unavailable: %s", exc)

    try:
        from edrak.agents.market_intelligence.graph import MarketIntelligence

        registry.register(WorkerType.MARKET_INTELLIGENCE, MarketIntelligence())
    except Exception as exc:
        logger.warning("Market intelligence worker unavailable: %s", exc)

    try:
        from edrak.agents.customer_trends.worker import CustomerTrendsWorker

        registry.register(WorkerType.CUSTOMER_TRENDS, CustomerTrendsWorker())
    except Exception as exc:
        logger.warning("Customer trends worker unavailable: %s", exc)

    try:
        from edrak.agents.internal_intelligence.graph import run_internal_intelligence

        class InternalWorker:
            worker_type = WorkerType.INTERNAL_INTELLIGENCE

            def run(self, task: Any) -> Any:
                return run_internal_intelligence(task)

        registry.register(WorkerType.INTERNAL_INTELLIGENCE, InternalWorker())
    except Exception as exc:
        logger.warning("Internal intelligence worker unavailable: %s", exc)

    return registry
