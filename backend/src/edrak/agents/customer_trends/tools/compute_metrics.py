"""compute_metrics: counts and statistics over stored evidence, with no model involved."""

import hashlib
import json
import math
import statistics
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, timedelta
from typing import Any, Literal

from pydantic import Field, ValidationError

from edrak.agents.customer_trends.schemas.analysis import MetricResult
from edrak.agents.customer_trends.schemas.common import (
    NonEmpty,
    ProcessingResponse,
    RunScope,
    StrictModel,
    ToolResponse,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.tools.base import (
    ToolContext,
    ToolSpec,
    error_response,
    finish_processing,
    invalid_input_response,
    unknown_batch_response,
)
from edrak.agents.customer_trends.tools.selection import group_key, load_items
from edrak.agents.customer_trends.utils.text import phrase_pattern, search_key

MIN_TREND_POINTS = 8
P90 = 0.9
MAX_COMPETITORS = 10

DESCRIPTION = """Compute exact numbers over the evidence collected so far, with no model guessing.

Use it for every count, share, growth rate or comparison you want to state. A finding may only
quote numbers that come from here or from analyze_text. Do NOT use it to read or search items
(use evidence_query).

Metrics (set `metric`; options go in `params`):
- volume_over_time: items per week or day by publication date. params {"bucket": "week"|"day"},
  weeks start on Monday. Items without a date are counted apart as `undated`.
- engagement_stats: mean, median and 90th percentile of interactions (likes, replies, shares,
  upvotes) per item, per platform and overall. Ratings and views are not interactions.
- trend_growth: percent change of a stored search interest series, from the mean of its first
  points to the mean of its last points. params {"keyword": "..."} for one series, every stored
  series when left out. A series under 8 points answers `insufficient_data: true`.
- share_of_voice: how many items mention the entity and each competitor, and each name's share of
  all mentions. params {"entity": "GitLab Duo", "competitors": ["GitHub Copilot"]}. Case and
  Arabic spelling variants are ignored.
- platform_mix and language_mix: item counts and percent by platform (news, web and review items
  are listed under their type) and by language.

`batch_ids` limits the batches; leave it out for everything in this run. The evidence metrics
also take params {"filters": {"platform": "reddit", "language": "ar", "text_contains": "copilot"}}
to count a part of the evidence.

The response carries a `metric_id`. To cite a result in a finding, put that id in the finding's
`metrics` as "metric_id" and copy the numbers you quote.

Example: {"metric": "volume_over_time", "params": {"bucket": "week"}}"""


class ComputeMetricsInput(RunScope):
    metric: Literal[
        "volume_over_time",
        "engagement_stats",
        "trend_growth",
        "share_of_voice",
        "platform_mix",
        "language_mix",
    ] = Field(description="Which metric to compute.")
    batch_ids: list[str] | None = Field(
        default=None, description="Batches to use; leave out for everything collected in this run."
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Options of the metric, see the tool description."
    )


class MetricResponse(ProcessingResponse):
    metric_id: str
    metric: str
    values: dict[str, Any]
    params: dict[str, Any]
    batch_ids: list[str]


class _FilteredParams(StrictModel):
    filters: EvidenceFilters = Field(default_factory=EvidenceFilters)


class VolumeParams(_FilteredParams):
    bucket: Literal["day", "week"] = "week"


class MixParams(_FilteredParams):
    pass


class ShareParams(_FilteredParams):
    entity: NonEmpty | None = None
    competitors: list[NonEmpty] = Field(default_factory=list, max_length=MAX_COMPETITORS)


class TrendParams(StrictModel):
    keyword: NonEmpty | None = None


def _round(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _percent(count: int, total: int) -> float:
    return _round(100 * count / total, 1) if total else 0.0


def percentile(ordered: Sequence[float], fraction: float) -> float:
    """Linear interpolation between the closest ranks of an ascending sequence."""
    position = fraction * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def volume_over_time(items: Sequence[EvidenceItem], bucket: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    undated = 0
    for item in items:
        if item.published_at is None:
            undated += 1
            continue
        day = item.published_at.astimezone(UTC).date()
        start = day - timedelta(days=day.weekday()) if bucket == "week" else day
        counts[start.isoformat()] += 1
    buckets = dict(sorted(counts.items()))
    values: dict[str, Any] = {
        "total": len(items),
        "dated": len(items) - undated,
        "undated": undated,
        "buckets": buckets,
    }
    if buckets:
        peak = max(buckets, key=lambda label: buckets[label])
        values.update(peak_bucket=peak, peak_count=buckets[peak])
    return values


def _engagement_summary(totals: list[int]) -> dict[str, Any]:
    ordered = sorted(totals)
    return {
        "items": len(ordered),
        "mean": _round(statistics.fmean(ordered)),
        "median": _round(statistics.median(ordered)),
        "p90": _round(percentile(ordered, P90)),
    }


def engagement_stats(items: Sequence[EvidenceItem]) -> dict[str, Any]:
    by_group: dict[str, list[int]] = {}
    for item in items:
        by_group.setdefault(group_key(item), []).append(item.engagement_total)
    return {
        "all": _engagement_summary([item.engagement_total for item in items]),
        "by_platform": {
            key: _engagement_summary(totals)
            for key, totals in sorted(by_group.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        },
    }


def _mix(keys: Sequence[str]) -> dict[str, Any]:
    counts = Counter(keys)
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return {
        "total": len(keys),
        "counts": dict(ordered),
        "percent": {key: _percent(n, len(keys)) for key, n in ordered},
    }


def platform_mix(items: Sequence[EvidenceItem]) -> dict[str, Any]:
    return _mix([group_key(item) for item in items])


def language_mix(items: Sequence[EvidenceItem]) -> dict[str, Any]:
    return _mix([item.language or "unknown" for item in items])


def share_of_voice(
    items: Sequence[EvidenceItem], entity: str, competitors: Sequence[str]
) -> dict[str, Any]:
    names = list(dict.fromkeys([entity, *competitors]))
    patterns = {name: phrase_pattern(name) for name in names}
    mentions = dict.fromkeys(names, 0)
    unmentioned = 0
    for item in items:
        text = search_key(item.text)
        hits = [name for name, pattern in patterns.items() if pattern.search(text)]
        for name in hits:
            mentions[name] += 1
        unmentioned += not hits
    total_mentions = sum(mentions.values())
    return {
        "items": len(items),
        "mentions": mentions,
        "percent": {name: _percent(n, total_mentions) for name, n in mentions.items()},
        "items_without_a_mention": unmentioned,
    }


def trend_growth(series_list: Sequence[TrendSeries]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for series in series_list:
        points = [value for _, value in series.points]
        if len(points) < MIN_TREND_POINTS:
            values[series.keyword] = {"insufficient_data": True, "points": len(points)}
            continue
        window = max(2, len(points) // 4)
        first, last = statistics.fmean(points[:window]), statistics.fmean(points[-window:])
        entry: dict[str, Any] = {
            "insufficient_data": False,
            "points": len(points),
            "window": window,
            "first_mean": _round(first),
            "last_mean": _round(last),
        }
        if first > 0:
            entry["percent_change"] = _round(100 * (last - first) / first, 1)
        else:
            entry["zero_baseline"] = True
        values[series.keyword] = entry
    return values


def metric_id_for(run_id: str, metric: str, params: dict[str, Any], batch_ids: list[str]) -> str:
    """The same run, metric, options and batches always give the same id."""
    payload = json.dumps(
        [run_id, metric, params, sorted(batch_ids)], sort_keys=True, ensure_ascii=False
    )
    return "m_" + hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _evidence_values(
    metric: str,
    params: VolumeParams | ShareParams | MixParams,
    items: list[EvidenceItem],
    ctx: ToolContext,
) -> dict[str, Any] | ToolResponse:
    if isinstance(params, VolumeParams):
        return volume_over_time(items, params.bucket)
    if isinstance(params, ShareParams):
        entity = params.entity or ctx.defaults.get("entity")
        if not entity:
            return error_response(
                "invalid_input", "share_of_voice needs params.entity (the name to count)"
            )
        return share_of_voice(items, str(entity), params.competitors)
    compute: dict[str, Callable[[Sequence[EvidenceItem]], dict[str, Any]]] = {
        "engagement_stats": engagement_stats,
        "platform_mix": platform_mix,
        "language_mix": language_mix,
    }
    return compute[metric](items)


def _compute(ctx: ToolContext, inp: ComputeMetricsInput) -> ToolResponse | ProcessingResponse:
    known = ctx.store.run_summary(ctx.run_id).batch_ids
    batch_ids = known if inp.batch_ids is None else inp.batch_ids
    unknown = [batch for batch in batch_ids if batch not in known]
    if unknown:
        return unknown_batch_response(unknown)
    params_models: dict[str, type[VolumeParams | ShareParams | TrendParams | MixParams]] = {
        "volume_over_time": VolumeParams,
        "share_of_voice": ShareParams,
        "trend_growth": TrendParams,
    }
    params_model = params_models.get(inp.metric, MixParams)
    try:
        params = params_model.model_validate(inp.params)
    except ValidationError as exc:
        return invalid_input_response(exc, prefix="params.")
    warnings: list[str] = []
    if isinstance(params, TrendParams):
        series = [
            s
            for s in ctx.store.get_trend_series(ctx.run_id, batch_ids)
            if params.keyword is None or s.keyword.casefold() == params.keyword.casefold()
        ]
        if not series:
            return error_response(
                "no_data", "no stored search interest series matches; call search_interest first"
            )
        values, count = trend_growth(series), len(series)
    else:
        items, total = load_items(
            ctx.store, ctx.run_id, params.filters.model_copy(update={"batch_ids": batch_ids})
        )
        if not items:
            return error_response(
                "no_data", "no stored items match; collect evidence first or widen the filters"
            )
        computed = _evidence_values(inp.metric, params, items, ctx)
        if isinstance(computed, ToolResponse):
            return computed
        values, count = computed, len(items)
        if total > len(items):
            warnings.append(f"computed over {len(items)} of {total} matching items")
        if inp.metric == "share_of_voice" and not any(values["mentions"].values()):
            warnings.append("no item mentions any of the names")
    stored_params = params.model_dump(mode="json", exclude_none=True)
    metric_id = metric_id_for(ctx.run_id, inp.metric, stored_params, batch_ids)
    ctx.store.save_metric(
        ctx.run_id,
        MetricResult(
            metric_id=metric_id,
            metric=inp.metric,
            values=values,
            params=stored_params,
            batch_ids=batch_ids,
        ),
    )
    return MetricResponse(
        count=count,
        warnings=warnings,
        metric_id=metric_id,
        metric=inp.metric,
        values=values,
        params=stored_params,
        batch_ids=batch_ids,
    )


async def compute_metrics(
    ctx: ToolContext, inp: ComputeMetricsInput
) -> ToolResponse | ProcessingResponse:
    started = time.perf_counter()
    return finish_processing(ctx, "compute_metrics", inp, _compute(ctx, inp), started)


SPEC = ToolSpec("compute_metrics", DESCRIPTION, ComputeMetricsInput, compute_metrics)
