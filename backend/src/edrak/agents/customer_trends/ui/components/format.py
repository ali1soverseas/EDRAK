"""Pure helpers of the developer UI: formatters, table builders and the right-to-left helper.

Nothing here imports Streamlit, so each one can be tested on its own.
"""

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from edrak.agents.customer_trends.graph import NODES
from edrak.agents.customer_trends.schemas.analysis import ThemeAggregate
from edrak.agents.customer_trends.schemas.evidence import EvidenceItem
from edrak.agents.customer_trends.schemas.findings import CustomerTrendsResult, Finding
from edrak.agents.customer_trends.schemas.trends import TrendSeries
from edrak.agents.customer_trends.tools.registry import COLLECTION_TOOL_NAMES, UNBUDGETED_ERRORS
from edrak.agents.customer_trends.tools.search_interest import summarize_series

NODE_ORDER = tuple(NODES)
ARGS_CHARS = 60
RTL_SHARE = 0.3
_ARABIC = re.compile("[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

STATUS_COLORS = {
    "complete": "green",
    "partial": "orange",
    "insufficient": "red",
    "ok": "green",
    "error": "red",
    "running": "blue",
    "done": "green",
    "pending": "gray",
    "skipped": "gray",
    "low": "red",
    "medium": "orange",
    "high": "green",
}


def badge(value: str) -> str:
    """Streamlit badge markdown for a status or a confidence level."""
    return f":{STATUS_COLORS.get(value, 'gray')}-badge[{value}]"


# right-to-left text


def is_rtl(text: str) -> bool:
    """True when the letters of the text are mostly Arabic."""
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and sum(1 for c in letters if _ARABIC.match(c)) / len(letters) >= RTL_SHARE


def rtl_html(text: str) -> str:
    """The text as an HTML block, right to left when it is mostly Arabic. HTML is escaped."""
    safe = html.escape(text).replace("\n", "<br>")
    if is_rtl(text):
        return f'<div dir="rtl" style="text-align: right">{safe}</div>'
    return f"<div>{safe}</div>"


# events


def args_summary(args: Mapping[str, Any]) -> str:
    parts = []
    for key, value in args.items():
        text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        parts.append(f"{key}={text[:ARGS_CHARS]}")
    return " ".join(parts)


def tool_rows(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per tool call, in the order the calls were reported."""
    return [
        {
            "time": str(event.get("ts", ""))[11:23],
            "branch": event.get("branch") or "",
            "tool": event.get("tool"),
            "args": args_summary(event.get("args") or {}),
            "provider": event.get("provider") or "",
            "fallback": "yes" if event.get("fallback_used") else "",
            "status": event.get("status"),
            "count": event.get("count"),
            "latency_ms": event.get("latency_ms"),
            "cost": round(float(event.get("cost") or 0.0), 4),
            "error": event.get("error_code") or "",
        }
        for event in events
        if event.get("type") == "tool_called"
    ]


def node_states(events: Iterable[Mapping[str, Any]], *, finished: bool) -> list[dict[str, str]]:
    """The state of each graph node: pending, running, done, skipped (a finished run that never
    reached it) or error. A node that ran twice shows its latest run."""
    latest: dict[str, str] = {}
    for event in events:
        node = event.get("node")
        if event.get("type") == "node_started":
            latest[str(node)] = "running"
        elif event.get("type") == "node_finished":
            latest[str(node)] = "error" if event.get("status") == "error" else "done"
    default = "skipped" if finished else "pending"
    return [{"node": node, "state": latest.get(node, default)} for node in NODE_ORDER]


def _seconds(events: Sequence[Mapping[str, Any]]) -> float:
    stamps = [datetime.fromisoformat(str(e["ts"])) for e in events if e.get("ts")]
    return (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else 0.0


def budget_meter(
    events: Sequence[Mapping[str, Any]], limits: Mapping[str, float]
) -> list[dict[str, Any]]:
    """Tool calls, cost and time used so far against the limits of the brief. Only provider calls
    count against the tool call limit, as in the run's budget."""
    calls = [
        e
        for e in events
        if e.get("type") == "tool_called"
        and e.get("tool") in COLLECTION_TOOL_NAMES
        and e.get("error_code") not in UNBUDGETED_ERRORS
    ]
    used = {
        "tool calls": float(len(calls)),
        "cost USD": round(sum(float(e.get("cost") or 0.0) for e in calls), 4),
        "seconds": round(_seconds(events), 1),
    }
    maxima = {
        "tool calls": float(limits["max_tool_calls"]),
        "cost USD": float(limits["max_cost_usd"]),
        "seconds": float(limits["max_seconds"]),
    }
    return [
        {
            "name": name,
            "used": used[name],
            "limit": maxima[name],
            "share": min(1.0, used[name] / maxima[name]) if maxima[name] else 0.0,
        }
        for name in used
    ]


def notices(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Gap and replan notices for the run panel."""
    lines = []
    for event in events:
        if event.get("type") == "gap_found":
            lines.append(f"gap ({event['severity']}): {event['description']}")
        elif event.get("type") == "replan":
            lines.append(f"replan {event['replan_count']}: closing {', '.join(event['gap_ids'])}")
    return lines


# the result


def coverage_rows(coverage: Mapping[str, Mapping[str, int]]) -> list[dict[str, Any]]:
    return [
        {"by": kind.removeprefix("by_").replace("_", " "), "name": name, "items": count}
        for kind, counts in coverage.items()
        for name, count in counts.items()
    ]


def theme_rows(aggregates: Iterable[ThemeAggregate]) -> list[dict[str, Any]]:
    return [
        {
            "theme": a.theme_label,
            "items": a.count,
            "share %": round(100 * a.share, 1),
            "negative %": round(100 * a.sentiment_mix.get("negative", 0.0), 1),
            "positive %": round(100 * a.sentiment_mix.get("positive", 0.0), 1),
            "growth": a.recent_growth,
            "platforms": ", ".join(f"{k} {v}" for k, v in a.by_platform.items()),
            "languages": ", ".join(f"{k} {v}" for k, v in a.by_language.items()),
        }
        for a in aggregates
    ]


def sentiment_rows(aggregates: Iterable[ThemeAggregate]) -> list[dict[str, Any]]:
    """Share of each sentiment inside each theme, for a stacked chart."""
    return [
        {"theme": a.theme_label, "sentiment": sentiment, "share": round(100 * share, 1)}
        for a in aggregates
        for sentiment, share in a.sentiment_mix.items()
    ]


def split_rows(aggregates: Iterable[ThemeAggregate], field: str) -> list[dict[str, Any]]:
    """Items per theme by platform (`by_platform`) or language (`by_language`)."""
    return [
        {"theme": a.theme_label, "name": name, "items": count}
        for a in aggregates
        for name, count in getattr(a, field).items()
    ]


def trend_summaries(series: Iterable[TrendSeries]) -> list[dict[str, Any]]:
    return [summarize_series(s) for s in series if s.points]


def trend_points(series: TrendSeries) -> list[dict[str, Any]]:
    return [{"date": day.isoformat(), "interest": value} for day, value in series.points]


def _cited(evidence_id: str, item: EvidenceItem | None) -> dict[str, Any]:
    if item is None:
        return {"id": evidence_id, "found": False, "platform": "", "url": None, "text": ""}
    return {
        "id": evidence_id,
        "found": True,
        "platform": item.platform.value if item.platform else item.source_type.value,
        "url": item.url,
        "snippet_only": item.snippet_only,
        "text": item.text,
    }


def finding_view(finding: Finding, items: Mapping[str, EvidenceItem]) -> dict[str, Any]:
    """What a finding card shows: the finding and its cited evidence, items missing from the
    store marked as such."""
    return {
        "id": finding.id,
        "type": finding.type.value,
        "confidence": finding.confidence.value,
        "claim": finding.claim,
        "metrics": dict(finding.metrics),
        "caveats": list(finding.caveats),
        "related_gaps": list(finding.related_gaps),
        "evidence": [
            _cited(evidence_id, items.get(evidence_id)) for evidence_id in finding.evidence_ids
        ],
    }


def evidence_rows(items: Iterable[EvidenceItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "platform": item.platform.value if item.platform else "",
            "source_type": item.source_type.value,
            "language": item.language or "",
            "published": item.published_at.date().isoformat() if item.published_at else "",
            "engagement": item.engagement_total,
            "snippet_only": item.snippet_only,
            "text": item.text[:140],
        }
        for item in items
    ]


def health_rows(report: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Provider health as table rows: open breakers first, then the details each provider gave."""
    shown = {"capabilities", "breaker_open", "failures"}
    rows = [
        {
            "provider": name,
            "status": "breaker open" if entry["breaker_open"] else "ok",
            "failures in a row": entry["failures"],
            "details": ", ".join(f"{k}: {v}" for k, v in entry.items() if k not in shown),
        }
        for name, entry in report.items()
    ]
    return sorted(rows, key=lambda row: (row["status"] == "ok", row["provider"]))


def provenance_rows(result: CustomerTrendsResult) -> dict[str, list[dict[str, Any]]]:
    """The provenance of a result as small tables: models, providers, timings, tool calls."""
    p = result.provenance
    return {
        "models": [{"role": role, "model": name} for role, name in p.get("models", {}).items()],
        "providers": [{"provider": n, "calls": c} for n, c in p.get("providers_used", {}).items()],
        "fallbacks": list(p.get("fallbacks", [])),
        "timings": [{"node": n, "ms": ms} for n, ms in p.get("node_timings_ms", {}).items()],
        "tool calls": [
            {"tool": tool, "calls": v["calls"], "errors": v["errors"], "cost USD": v["cost_usd"]}
            for tool, v in p.get("tool_calls", {}).items()
        ],
        "provider health": health_rows(p.get("provider_health", {})),
    }
