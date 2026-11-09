"""The evidence profile: findings about the evidence itself, written by code from what is stored.

A reader of the result needs more than what customers said: how big the sample is, how its
sentiment splits, how far it reaches in time, which companies and terms it covers, what is
missing and how sure the result is. Code already counts all of that (the metrics, the store, the
gaps), so these findings are built from those numbers and never by the writer model. Each one
cites the metric it comes from and real evidence items, and goes through the same checks as every
other finding (`check_finding`). They describe the sample, not the market.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from edrak.agents.customer_trends.assembly import overall_confidence
from edrak.agents.customer_trends.gaps import Gap, run_status
from edrak.agents.customer_trends.schemas.analysis import MetricResult
from edrak.agents.customer_trends.schemas.common import Confidence, FindingType, SourceType
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.schemas.findings import PROFILE_PREFIX, Finding
from edrak.agents.customer_trends.schemas.task import TaskBrief
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.compute_metrics import metric_id_for
from edrak.agents.customer_trends.tools.submit_findings import (
    claim_numbers,
    matches,
    own_numbers,
)

SENTIMENT_OVERVIEW = "sentiment_overview"
EVIDENCE_PER_FINDING = 6
MAX_PROFILE_FINDINGS = 9  # the findings built below, at most
MIN_VENDOR_ITEMS = 10  # fewer items naming a company than this cannot carry a comparison
SENTIMENT_ORDER = ("positive", "neutral", "negative", "mixed")
SOURCE_NAMES = {
    SourceType.NEWS: "news",
    SourceType.WEB: "web",
    SourceType.REVIEW: "review",
    SourceType.SOCIAL_POST: "social post",
    SourceType.SOCIAL_COMMENT: "social comment",
    SourceType.TREND_POINT: "search interest",
}


def _n(value: float) -> str:
    """A number as the claim writes it: whole numbers without a decimal point."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _join(parts: Sequence[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def store_sentiment_overview(
    store: EvidenceStore, run_id: str, answer: Mapping[str, Any], batch_ids: list[str]
) -> str | None:
    """Keep the overall sentiment split that `analyze_text` returns, as a metric findings can
    cite. Returns its metric id, or None when the analysis gave no sentiment."""
    sentiment = answer.get("sentiment") or {}
    counts = sentiment.get("counts")
    analyzed = answer.get("analyzed")
    if not counts or not analyzed:
        return None
    metric_id = metric_id_for(run_id, SENTIMENT_OVERVIEW, {}, batch_ids)
    store.save_metric(
        run_id,
        MetricResult(
            metric_id=metric_id,
            metric=SENTIMENT_OVERVIEW,
            values={
                "analyzed": int(analyzed),
                "total_items": int(answer.get("total_items") or analyzed),
                "counts": counts,
                "percent": sentiment.get("percent", {}),
            },
            batch_ids=batch_ids,
        ),
    )
    return metric_id


@dataclass
class Profile:
    """What the profile findings are built from."""

    store: EvidenceStore
    brief: TaskBrief
    run_id: str
    metrics: dict[str, MetricResult]
    items: list[EvidenceItem]
    by_source_type: dict[str, int]
    min_language_items: int

    def sample_ids(self, items: Sequence[EvidenceItem] | None = None) -> list[str]:
        """Up to a few ids of stored items, spread over the platforms, to cite."""
        pool = list(items if items is not None else self.items)
        chosen: list[str] = []
        seen: set[str] = set()
        for item in pool:
            key = str(item.platform or item.source_type)
            if key not in seen:
                seen.add(key)
                chosen.append(item.id)
        chosen += [i.id for i in pool if i.id not in chosen]
        return chosen[:EVIDENCE_PER_FINDING]

    def metric(self, name: str) -> MetricResult | None:
        return self.metrics.get(name)

    def record(self, name: str, values: dict[str, Any]) -> MetricResult:
        """Store counts this module computed itself as a metric, so the finding that quotes them
        cites a stored record like every other number does."""
        record = MetricResult(
            metric_id=metric_id_for(self.run_id, name, {}, []), metric=name, values=values
        )
        self.store.save_metric(self.run_id, record)
        return record


def _finding(
    fid: str,
    kind: FindingType,
    claim: str,
    profile: Profile,
    *,
    numbers: Mapping[str, float] | None = None,
    cites: Sequence[MetricResult | None] = (),
    caveats: Sequence[str] = (),
    evidence: Sequence[str] | None = None,
    gaps: Sequence[str] = (),
) -> Finding:
    """A finding whose claim numbers are all in its metrics: those given, the cited metrics', and
    any other number the claim writes (a date, a count of gaps) copied as computed."""
    metrics: dict[str, float | int | str] = dict(numbers or {})
    for record in cites:
        if record is not None:
            metrics[f"{record.metric}_metric_id"] = record.metric_id
    known = own_numbers(metrics)
    for record in cites:
        if record is not None:
            known += list(_flatten(record.values))
    for position, value in enumerate(claim_numbers(claim)):
        if not any(matches(value, k) for k in known):
            metrics[f"claim_number_{position}"] = value
            known.append(value)
    return Finding(
        id=fid,
        type=kind,
        claim=claim,
        confidence=Confidence.MEDIUM,
        evidence_ids=list(evidence if evidence is not None else profile.sample_ids()),
        metrics=metrics,
        caveats=list(caveats),
        related_gaps=list(gaps),
    )


def _flatten(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return [float(value)]
    if isinstance(value, dict):
        return [n for inner in value.values() for n in _flatten(inner)]
    if isinstance(value, list | tuple):
        return [n for inner in value for n in _flatten(inner)]
    return []


def _scope(p: Profile) -> Finding | None:
    """Which companies and terms of the brief the collected items actually mention."""
    terms = list(dict.fromkeys([p.brief.entity, *p.brief.competitors, *p.brief.focus_terms]))
    total = len(p.items)
    if not terms or not total:
        return None
    hits: dict[str, int] = {}
    first_hit: list[EvidenceItem] = []
    for term in terms:
        found = p.store.query(
            p.run_id, EvidenceFilters(text_contains=term), limit=EVIDENCE_PER_FINDING
        )
        hits[term] = found.total
        first_hit = first_hit or found.items
    mentioned = [f"{term} ({_n(n)})" for term, n in hits.items() if n]
    silent = [term for term, n in hits.items() if not n]
    claim = f"Of the {_n(total)} collected items, "
    claim += (
        f"those naming the terms of the brief are {_join(mentioned)}"
        if mentioned
        else ("none names any term of the brief")
    )
    claim += "."
    if silent:
        claim += f" No collected item mentions {_join(silent)}."
    return _finding(
        f"{PROFILE_PREFIX}scope",
        FindingType.RISK,
        claim,
        p,
        cites=[p.record("scope_coverage", {"items": total, "mentions": hits})],
        caveats=[
            "mentions are found by matching text, so an item can concern a segment or region "
            "without naming it",
            "terms with no mention are not covered by the evidence",
        ],
        evidence=p.sample_ids(first_hit) if first_hit else None,
    )


def _composition(p: Profile) -> Finding | None:
    """How many items of which kind, and how many are only a snippet."""
    total = len(p.items)
    if not total:
        return None
    kinds = [
        f"{_n(n)} {SOURCE_NAMES.get(SourceType(k), k)}"
        for k, n in sorted(p.by_source_type.items(), key=lambda pair: -pair[1])
    ]
    snippets = sum(item.snippet_only for item in p.items)
    claim = f"The run collected {_n(total)} items: {_join(kinds)}."
    record = p.record(
        "evidence_composition",
        {"items": total, "by_source_type": p.by_source_type, "snippet_only": snippets},
    )
    caveats = ["counts describe what was collected, not what exists"]
    if snippets:
        claim += (
            f" {_n(snippets)} of them are snippet-only, with no full text and no engagement counts."
        )
        caveats.append("snippet-only items support a headline-level reading only")
    return _finding(
        f"{PROFILE_PREFIX}composition", FindingType.RISK, claim, p, cites=[record], caveats=caveats
    )


def _sentiment(p: Profile) -> Finding | None:
    record = p.metric(SENTIMENT_OVERVIEW)
    if record is None:
        return None
    counts, percent = record.values["counts"], record.values.get("percent", {})
    analyzed = record.values["analyzed"]
    parts = [
        f"{_n(counts[label])} {label} ({_n(percent.get(label, 0.0))}%)"
        for label in SENTIMENT_ORDER
        if counts.get(label)
    ]
    claim = (
        f"The {_n(analyzed)} analyzed items split into {_join(parts)}. "
        "This is the sentiment of the collected sample, not of the whole customer population."
    )
    return _finding(
        f"{PROFILE_PREFIX}sentiment",
        FindingType.SENTIMENT,
        claim,
        p,
        cites=[record],
        caveats=["model-labelled sentiment of a small, non-random sample"],
    )


def _temporal(p: Profile) -> Finding | None:
    record = p.metric("volume_over_time")
    if record is None or not record.values.get("total"):
        return None
    values = record.values
    dated, total = values["dated"], values["total"]
    claim = f"Only {_n(dated)} of {_n(total)} items carry a publication date"
    if values.get("peak_bucket"):
        claim += (
            f"; the busiest period starts {values['peak_bucket']} with "
            f"{_n(values['peak_count'])} items"
        )
    claim += "."
    has_series = bool(p.store.get_trend_series(p.run_id))
    caveats = ["publication dates show when items were posted, not how demand changed"]
    if not has_series:
        claim += (
            " No search-interest series was collected, so this does not show sustained momentum."
        )
        caveats.append("a few dated items do not establish a trend over time")
    return _finding(
        f"{PROFILE_PREFIX}temporal", FindingType.TREND, claim, p, cites=[record], caveats=caveats
    )


def _voice(p: Profile) -> Finding | None:
    record = p.metric("share_of_voice")
    if record is None:
        return None
    mentions = record.values.get("mentions", {})
    listed = [f"{name} {_n(n)}" for name, n in mentions.items()]
    claim = (
        f"Among {_n(record.values['items'])} items, mentions of the companies are "
        f"{_join(listed)}, and {_n(record.values['items_without_a_mention'])} items name none "
        "of them. These counts show how the sample is made up, not market share or popularity."
    )
    return _finding(
        f"{PROFILE_PREFIX}voice",
        FindingType.COMPETITOR_GAP,
        claim,
        p,
        cites=[record],
        caveats=["items were found by searching for the company names"],
    )


def _comparability(p: Profile) -> Finding | None:
    record = p.metric("share_of_voice")
    if record is None:
        return None
    mentions = record.values.get("mentions", {})
    thin = {name: n for name, n in mentions.items() if n < MIN_VENDOR_ITEMS}
    if not thin:
        return None
    listed = [f"{name} ({_n(n)})" for name, n in thin.items()]
    claim = (
        f"The evidence is insufficient for a balanced comparison of sentiment, needs, pricing or "
        f"adoption between the companies: {_join(listed)} each appear in fewer than "
        f"{MIN_VENDOR_ITEMS} items."
    )
    return _finding(
        f"{PROFILE_PREFIX}comparability",
        FindingType.COMPETITOR_GAP,
        claim,
        p,
        numbers={
            "min_items": MIN_VENDOR_ITEMS,
            **{f"items_{i}": n for i, n in enumerate(thin.values())},
        },
        cites=[record],
        caveats=["differences between companies in this sample may only reflect sample size"],
    )


def _engagement(p: Profile) -> Finding | None:
    record = p.metric("engagement_stats")
    if record is None:
        return None
    overall = record.values["all"]
    claim = (
        f"Recorded engagement per item has a mean of {_n(overall['mean'])}, a median of "
        f"{_n(overall['median'])} and a 90th percentile of {_n(overall['p90'])} across "
        f"{_n(overall['items'])} items."
    )
    caveats = ["engagement is only recorded by some platforms"]
    if not overall["mean"] and not overall["p90"]:
        claim += (
            " Engagement counts were not available, so zero here does not mean zero real-world"
            " engagement."
        )
        caveats.append("news and web snippets carry no engagement counts")
    return _finding(
        f"{PROFILE_PREFIX}engagement", FindingType.RISK, claim, p, cites=[record], caveats=caveats
    )


def _language(p: Profile) -> Finding | None:
    record = p.metric("language_mix")
    if record is None:
        return None
    counts = record.values.get("counts", {})
    wanted = list(dict.fromkeys([*p.brief.languages, *counts]))
    listed = [f"{_n(counts.get(code, 0))} in {code}" for code in wanted if code != "unknown"]
    thin = [code for code in p.brief.languages if counts.get(code, 0) < p.min_language_items]
    claim = f"The collected items are {_join(listed)}."
    if thin:
        claim += (
            f" With fewer than {p.min_language_items} items in {_join(thin)}, conclusions for "
            f"{'that language' if len(thin) == 1 else 'those languages'} would not be robust."
        )
    return _finding(
        f"{PROFILE_PREFIX}language",
        FindingType.RISK,
        claim,
        p,
        numbers={"min_language_items": p.min_language_items},
        cites=[record],
        caveats=["the language mix reflects the sources searched"],
    )


def _gaps(p: Profile, gaps: Sequence[Gap], others: Sequence[Finding]) -> Finding:
    status = run_status(gaps)
    confidence = overall_confidence([*others], gaps).value
    claim = f"The result is {status} with {confidence} overall confidence"
    if gaps:
        claim += (
            f" and {_n(len(gaps))} open gap(s): " + "; ".join(g.description for g in gaps) + "."
        )
    else:
        claim += " and no open coverage gap."
    record = p.record(
        "run_status",
        {
            "status": status,
            "overall_confidence": confidence,
            "open_gaps": len(gaps),
            "gaps": [g.description for g in gaps],
        },
    )
    return _finding(
        f"{PROFILE_PREFIX}gaps",
        FindingType.RISK,
        claim,
        p,
        cites=[record],
        caveats=["gaps are rule-based checks of coverage, not a judgement of the evidence"],
        gaps=[g.id for g in gaps],
    )


def profile_findings(
    store: EvidenceStore,
    brief: TaskBrief,
    metric_ids: Mapping[str, str],
    gaps: Sequence[Gap],
    others: Sequence[Finding],
    *,
    min_language_items: int,
) -> list[Finding]:
    """The profile findings of a run, ending with its status, confidence and open gaps.
    `others` are the findings already written; their confidence feeds the overall confidence."""
    run_id = brief.run_id
    summary = store.run_summary(run_id)
    if not summary.evidence_count:
        return []
    items = store.query(run_id, limit=summary.evidence_count, sample="recent").items
    metrics = {
        name: record
        for name, metric_id in metric_ids.items()
        if (record := store.get_metric(run_id, metric_id)) is not None
    }
    profile = Profile(
        store, brief, run_id, metrics, items, summary.by_source_type, min_language_items
    )
    built = [
        build(profile)
        for build in (
            _scope,
            _composition,
            _sentiment,
            _temporal,
            _voice,
            _comparability,
            _engagement,
            _language,
        )
    ]
    written = [f for f in built if f is not None]
    return [*written, _gaps(profile, gaps, [*others, *written])]
