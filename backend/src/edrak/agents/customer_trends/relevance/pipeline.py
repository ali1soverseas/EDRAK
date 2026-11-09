"""One pass of the relevance filter over a run's stored comments.

Stage A (rules) and Stage B (keywords) are free. Stage C (the model) sees only what is left. Every
verdict is written back as a mark on the stored item; nothing is removed.
"""

from dataclasses import dataclass, field
from typing import Any

from edrak.agents.customer_trends.deps import WorkerDeps
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.providers.budget import FINISH_RESERVE
from edrak.agents.customer_trends.relevance import accounting
from edrak.agents.customer_trends.relevance.cascade import free_stages
from edrak.agents.customer_trends.relevance.classify import classify
from edrak.agents.customer_trends.relevance.models import (
    UNKNOWN_PLATFORM,
    Comment,
)
from edrak.agents.customer_trends.relevance.parents import PostIndex
from edrak.agents.customer_trends.relevance.terms import TermMatcher, match_terms
from edrak.agents.customer_trends.schemas.common import SourceType
from edrak.agents.customer_trends.schemas.task import TaskBrief

log = get_logger(__name__)

POSTS = [SourceType.SOCIAL_POST]


@dataclass
class PassResult:
    """What one pass judged. The run totals are read from the store with `run_stats`."""

    judged: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    sent_to_llm: int = 0
    kept: int = 0
    warnings: list[str] = field(default_factory=list)

    def event(self) -> dict[str, Any]:
        return {
            "type": "relevance_filtered",
            "comments_in": self.judged,
            "dropped": self.dropped,
            "sent_to_llm": self.sent_to_llm,
            "kept": self.kept,
        }


def prompt_values(brief: TaskBrief) -> dict[str, Any]:
    return {
        "question": brief.question,
        "entity": brief.entity,
        "aliases": brief.aliases,
        "products": brief.products,
        "competitors": brief.competitors,
    }


def filtered_types(deps: WorkerDeps) -> list[str]:
    """The source types the filter handles in this run: none when it is switched off."""
    settings = deps.settings
    return (
        [t.value for t in settings.relevance_item_types]
        if settings.relevance_filter_enabled
        else []
    )


def min_relevant(deps: WorkerDeps) -> int:
    """The fewest relevant comments a platform should keep, or 0 when the filter is off."""
    settings = deps.settings
    return settings.relevance_min_relevant if settings.relevance_filter_enabled else 0


def classify_budget_s(deps: WorkerDeps) -> float:
    """The model calls of the filter may use at most half of the time kept back for analysis and
    writing, and never more than RELEVANCE_TIMEOUT_S."""
    reserve = deps.budget.snapshot().limits.max_seconds * FINISH_RESERVE / 2
    return max(0.0, min(deps.settings.relevance_timeout_s, reserve))


def run_stats(deps: WorkerDeps, run_id: str) -> dict[str, Any]:
    settings = deps.settings
    types = [t.value for t in settings.relevance_item_types]
    if not settings.relevance_filter_enabled:
        return accounting.disabled(types)
    return accounting.summarize(deps.store.relevance_rows(run_id, types), types)


async def filter_run(deps: WorkerDeps, brief: TaskBrief) -> PassResult:
    """Judge every stored comment (of the configured types) that has no verdict yet."""
    settings, store = deps.settings, deps.store
    result = PassResult()
    items, judged = store.judged_items(brief.run_id, settings.relevance_item_types)
    if all(item.id in judged for item in items):
        return result

    posts = PostIndex(store.judged_items(brief.run_id, POSTS)[0])
    comments = []
    for item in items:
        parent = posts.parent_of(item)
        comments.append(
            Comment(
                id=item.id,
                text=item.text,
                platform=item.platform.value if item.platform else UNKNOWN_PLATFORM,
                parent_key=parent.key,
                parent_text=parent.text,
                already=item.id in judged,
            )
        )

    verdicts, pending, duplicates = free_stages(comments, TermMatcher(match_terms(brief)))

    outcome = await classify(
        pending,
        model_for=lambda: deps.model("relevance"),
        prompt_values=prompt_values(brief),
        batch_size=settings.relevance_batch_size,
        parent_chars=settings.relevance_parent_chars,
        comment_chars=settings.relevance_comment_chars,
        concurrency=settings.relevance_concurrency,
        timeout_s=classify_budget_s(deps),
    )
    verdicts.update(outcome.verdicts)

    marks = {comment_id: verdict.mark() for comment_id, verdict in verdicts.items()}
    by_id = {item.id: item for item in items}
    for first, repeats in duplicates.repeats.items():
        earlier = int(by_id[first].metadata.get("duplicate_count", 0))
        marks.setdefault(first, {})["duplicate_count"] = earlier + repeats
    store.mark_relevance(brief.run_id, marks)

    result.judged = len(verdicts)
    result.sent_to_llm = outcome.sent
    result.warnings = outcome.warnings
    for verdict in verdicts.values():
        if verdict.relevance == "relevant":
            result.kept += 1
        else:
            result.dropped[verdict.reason] = result.dropped.get(verdict.reason, 0) + 1
    log.info("relevance_filtered", judged=result.judged, kept=result.kept, dropped=result.dropped)
    return result
