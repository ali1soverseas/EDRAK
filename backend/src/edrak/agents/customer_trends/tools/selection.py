"""Which stored items the processing tools read."""

from datetime import UTC, datetime
from typing import Literal

from edrak.agents.customer_trends.schemas.common import SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore

Order = Literal["top", "recent"]

# Well above what a run collects (the deep cap is 1000 per call), so metrics see every item.
ALL_ITEMS = 100_000
_NEVER = datetime.min.replace(tzinfo=UTC)


def group_key(item: EvidenceItem) -> str:
    """The platform, or the source type for items that belong to no platform."""
    return item.platform.value if item.platform else item.source_type.value


def _ranked(items: list[EvidenceItem], order: Order) -> list[EvidenceItem]:
    if order == "top":
        return sorted(
            items,
            key=lambda i: (-i.engagement_total, -(i.published_at or _NEVER).timestamp(), i.id),
        )
    return sorted(items, key=lambda i: (-(i.published_at or _NEVER).timestamp(), i.id))


def load_items(
    store: EvidenceStore,
    run_id: str,
    filters: EvidenceFilters,
    *,
    limit: int = ALL_ITEMS,
    order: Order = "top",
) -> tuple[list[EvidenceItem], int]:
    """Up to `limit` matching items, best first, and how many items match in total.

    Trend point items are leftovers of the search interest tool, one sentence per series, not
    something people wrote, so they never take part in counting or theme analysis.
    """
    kinds = [filters.source_type] if filters.source_type is not None else list(SourceType)
    items: list[EvidenceItem] = []
    total = 0
    for kind in kinds:
        if kind is SourceType.TREND_POINT:
            continue
        found = store.query(
            run_id, filters.model_copy(update={"source_type": kind}), limit=limit, sample=order
        )
        items.extend(found.items)
        total += found.total
    return _ranked(items, order)[:limit], total
