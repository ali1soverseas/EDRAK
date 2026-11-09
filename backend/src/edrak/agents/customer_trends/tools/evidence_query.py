"""evidence_query: read a few stored items back, for quoting and citing."""

import time
from typing import Any, Literal

from pydantic import Field

from edrak.agents.customer_trends.schemas.common import (
    ProcessingResponse,
    RunScope,
    ToolResponse,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.tools.base import ToolContext, ToolSpec, finish_processing
from edrak.agents.customer_trends.tools.selection import group_key
from edrak.agents.customer_trends.utils.labels import normalize_label
from edrak.agents.customer_trends.utils.text import truncate

MAX_ITEMS = 20
TEXT_CHARS = 500
LISTED_THEMES = 10

DESCRIPTION = """Use it to read items back from the evidence stored in this run, to quote them or to
check a claim before citing them. At most 20 items come back, each text cut to 500 characters.

Filters (all optional, combined with AND): platform, source_type, language (ar, en), text_contains
(case and Arabic spelling variants ignored), theme (a theme label found by analyze_text),
min_engagement (likes, replies, shares and upvotes added up), since and until (publication dates,
YYYY-MM-DD) and batch_ids. `sample` picks which matches come back: "top" the most engaged,
"recent" the newest, "random" a draw.

The answer holds `total`, the number of items that match, and the items with their `id`: put those
ids in a finding's evidence_ids. Do NOT use it to count (use compute_metrics) or to collect new
evidence (use the collection tools).

Example: {"filters": {"platform": "reddit", "language": "en", "text_contains": "pricing"},
"limit": 5, "sample": "top"}"""


class EvidenceQueryInput(RunScope):
    filters: EvidenceFilters = Field(
        default_factory=EvidenceFilters, description="Which stored items to look at."
    )
    limit: int = Field(default=10, ge=1, description="How many items to return, at most 20.")
    sample: Literal["top", "recent", "random"] = Field(
        default="top", description="Which of the matching items come back."
    )


class EvidenceQueryResponse(ProcessingResponse):
    total: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)


def _view(item: EvidenceItem) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": item.id,
        "platform": group_key(item),
        "source_type": item.source_type.value,
        "language": item.language,
        "published": item.published_at.date().isoformat() if item.published_at else None,
        "engagement": item.engagement,
        "url": item.url,
        "text": truncate(item.text, TEXT_CHARS),
    }
    if item.snippet_only:
        view["snippet_only"] = True
    return view


def _resolve_theme(ctx: ToolContext, theme: str) -> tuple[str, list[str]]:
    """The stored label that `theme` names, and warnings when there is none."""
    aggregates = ctx.store.get_aggregates(ctx.run_id)
    wanted = normalize_label(theme)
    for aggregate in aggregates:
        if normalize_label(aggregate.theme_label) == wanted:
            return aggregate.theme_label, []
    if not aggregates:
        return theme, ["no themes are stored yet; run analyze_text first"]
    labels = ", ".join(a.theme_label for a in aggregates[:LISTED_THEMES])
    return theme, [f"no stored theme matches '{theme}'; stored themes include: {labels}"]


def _query(ctx: ToolContext, inp: EvidenceQueryInput) -> EvidenceQueryResponse:
    warnings: list[str] = []
    filters = inp.filters
    if filters.theme is not None:
        label, theme_warnings = _resolve_theme(ctx, filters.theme)
        filters = filters.model_copy(update={"theme": label})
        warnings.extend(theme_warnings)
    limit = min(inp.limit, MAX_ITEMS)
    if inp.limit > MAX_ITEMS:
        warnings.append(f"limit lowered to {MAX_ITEMS}, the most one call returns")
    found = ctx.store.query(ctx.run_id, filters, limit=limit, sample=inp.sample)
    if found.total == 0 and not warnings:
        warnings.append("no stored item matches these filters")
    elif found.total > len(found.items):
        warnings.append(f"{found.total - len(found.items)} more matching items were not returned")
    return EvidenceQueryResponse(
        count=len(found.items),
        total=found.total,
        items=[_view(item) for item in found.items],
        warnings=warnings,
    )


async def evidence_query(
    ctx: ToolContext, inp: EvidenceQueryInput
) -> ToolResponse | ProcessingResponse:
    started = time.perf_counter()
    return finish_processing(ctx, "evidence_query", inp, _query(ctx, inp), started)


SPEC = ToolSpec("evidence_query", DESCRIPTION, EvidenceQueryInput, evidence_query)
