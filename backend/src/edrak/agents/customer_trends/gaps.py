"""Deterministic coverage rules: what is missing from the evidence, and what to do about it.

The rules are pure functions of the brief, the use case configuration and counts read from the
store. A critical gap may trigger one replan; the rest travel into the result. These are the
worker's own quality gates and do not replace the Verification stage.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from edrak.agents.customer_trends.schemas.common import Platform, SourceType, StrictModel
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.store.evidence_store import UNKNOWN, EvidenceStore
from edrak.agents.customer_trends.usecases import UseCaseConfig

Severity = Literal["critical", "minor"]

EVIDENCE_TOTAL = "evidence_total"
PLATFORMS = "platforms"
TREND_SERIES = "trend_series"
REVIEWS = "reviews"
LANGUAGE_PREFIX = "language_"
BRANCH_PREFIX = "branch_"
RELEVANT_COMMENTS_PREFIX = "relevant_comments_"


class Gap(StrictModel):
    id: str
    severity: Severity
    description: str
    suggested_action: str


@dataclass(frozen=True)
class Coverage:
    """What the store holds, without the trend point sentences (nobody wrote those)."""

    total: int
    by_platform: Mapping[str, int]
    by_language: Mapping[str, int]
    reviews: int
    trend_series: int
    # What the relevance filter judged and kept, per platform. The counts above are of kept items
    # only, because the store hides what the filter marked irrelevant.
    comments_judged: Mapping[str, int] = field(default_factory=dict)
    comments_kept: Mapping[str, int] = field(default_factory=dict)


def coverage_of(
    store: EvidenceStore, run_id: str, *, filtered_types: Sequence[str] = ()
) -> Coverage:
    """Counts of what the store shows. `filtered_types` are the source types the relevance filter
    handles; with them, the comments it judged and kept per platform are counted too."""
    judged: dict[str, int] = {}
    kept: dict[str, int] = {}
    for row in store.relevance_rows(run_id, filtered_types) if filtered_types else []:
        judged[row["platform"]] = judged.get(row["platform"], 0) + row["n"]
        if row["relevance"] != "irrelevant":
            kept[row["platform"]] = kept.get(row["platform"], 0) + row["n"]
    trend_point = SourceType.TREND_POINT.value
    by_type = store.count_by(run_id, "source_type", exclude_source_type=trend_point)
    by_platform = store.count_by(run_id, "platform", exclude_source_type=trend_point)
    by_platform.pop(UNKNOWN, None)
    return Coverage(
        total=sum(by_type.values()),
        by_platform=by_platform,
        by_language=store.count_by(run_id, "language", exclude_source_type=trend_point),
        reviews=by_type.get(SourceType.REVIEW.value, 0),
        trend_series=len(store.get_trend_series(run_id)),
        comments_judged=judged,
        comments_kept=kept,
    )


def find_gaps(
    brief: TaskBrief,
    config: UseCaseConfig,
    coverage: Coverage,
    *,
    review_targets_planned: bool = False,
    branch_errors: Mapping[str, str] | None = None,
    min_relevant_comments: int = 0,
) -> list[Gap]:
    """Every rule that fails, in a stable order. `min_relevant_comments` is the least a platform
    should keep after the relevance filter; 0 turns that rule off."""
    gaps = [
        Gap(
            id=f"{BRANCH_PREFIX}{name}",
            severity="critical",
            description=f"the {name} branch reported an error: {error}",
            suggested_action=f"retry the {name} collection with narrower, simpler requests",
        )
        for name, error in (branch_errors or {}).items()
        if error
    ]
    gaps += _volume_gaps(config, coverage)
    gaps += _trend_gaps(brief, config, coverage)
    gaps += _review_gaps(brief, config, coverage, review_targets_planned)
    gaps += _language_gaps(brief, config, coverage)
    gaps += _comment_gaps(coverage, min_relevant_comments)
    return gaps


def _volume_gaps(config: UseCaseConfig, coverage: Coverage) -> list[Gap]:
    gaps: list[Gap] = []
    if coverage.total < config.min_evidence_total:
        gaps.append(
            Gap(
                id=EVIDENCE_TOTAL,
                severity="critical",
                description=(
                    f"only {coverage.total} evidence items were collected; at least "
                    f"{config.min_evidence_total} are needed"
                ),
                suggested_action="collect more items with broader queries on more platforms",
            )
        )
    adequate = [p for p, n in coverage.by_platform.items() if n >= config.min_platform_items]
    if len(adequate) < config.min_platforms:
        missing = [p.value for p in Platform if p.value not in adequate]
        gaps.append(
            Gap(
                id=PLATFORMS,
                severity="critical",
                description=(
                    f"{len(adequate)} platform(s) have at least {config.min_platform_items} "
                    f"items; at least {config.min_platforms} are needed"
                ),
                suggested_action=(
                    f"collect at least {config.min_platform_items} items from more of: "
                    + ", ".join(missing)
                ),
            )
        )
    return gaps


def _trend_gaps(brief: TaskBrief, config: UseCaseConfig, coverage: Coverage) -> list[Gap]:
    if coverage.trend_series or not (config.require_trend_series or "demand" in brief.focus):
        return []
    return [
        Gap(
            id=TREND_SERIES,
            severity="critical" if config.require_trend_series else "minor",
            description="no search interest series was collected, so demand over time is unknown",
            suggested_action="run search_interest for the entity, its category and the competitors",
        )
    ]


def _review_gaps(
    brief: TaskBrief, config: UseCaseConfig, coverage: Coverage, targets_planned: bool
) -> list[Gap]:
    rule = config.reviews
    if not (rule.required_with_competitors and brief.competitors and targets_planned):
        return []
    if coverage.reviews:
        return []
    return [
        Gap(
            id=REVIEWS,
            severity=rule.severity,
            description=(
                "competitors are named and review targets were planned, but no review was collected"
            ),
            suggested_action=(
                "fetch reviews for the planned competitor apps, or check the target ids"
            ),
        )
    ]


def _language_gaps(brief: TaskBrief, config: UseCaseConfig, coverage: Coverage) -> list[Gap]:
    return [
        Gap(
            id=f"{LANGUAGE_PREFIX}{language}",
            severity="minor",
            description=(
                f"{coverage.by_language.get(language, 0)} item(s) in '{language}'; at least "
                f"{config.min_language_items} are needed"
            ),
            suggested_action=f"search again with queries written in '{language}'",
        )
        for language in brief.languages
        if coverage.by_language.get(language, 0) < config.min_language_items
    ]


def _comment_gaps(coverage: Coverage, minimum: int) -> list[Gap]:
    """A note, never a blocker: a platform whose comments the filter judged but left under the
    minimum has little to say, whatever its raw volume was."""
    return [
        Gap(
            id=f"{RELEVANT_COMMENTS_PREFIX}{platform}",
            severity="minor",
            description=(
                f"only {coverage.comments_kept.get(platform, 0)} relevant comment(s) on {platform} "
                f"remain after filtering {judged}; at least {minimum} are expected, so the "
                "relevant comment volume was low"
            ),
            suggested_action=(
                f"collect comments under more posts on {platform}, or check that the queries match "
                "the topic"
            ),
        )
        for platform, judged in sorted(coverage.comments_judged.items())
        if minimum > 0 and judged > 0 and coverage.comments_kept.get(platform, 0) < minimum
    ]


def critical_gaps(gaps: Iterable[Gap]) -> list[Gap]:
    return [gap for gap in gaps if gap.severity == "critical"]


def run_status(gaps: Iterable[Gap]) -> Literal["complete", "partial", "insufficient"]:
    """`insufficient` when the evidence total is under the minimum, `partial` when any other
    critical gap remains, `complete` otherwise (minor gaps are listed, not blocking)."""
    open_gaps = list(gaps)
    if any(gap.id == EVIDENCE_TOTAL for gap in open_gaps):
        return "insufficient"
    return "partial" if critical_gaps(open_gaps) else "complete"
