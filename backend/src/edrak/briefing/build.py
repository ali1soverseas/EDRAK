"""Assemble the typed half of a brief (document version 3) from stored metrics."""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiosqlite

from edrak.briefing.fill import build_cards
from edrak.briefing.layout import coverage, kpi_strip, sections
from edrak.briefing.propose import apply_copy, propose_copy
from edrak.contracts import BusinessRequest, WorkerResult
from edrak.db.repositories import metrics as metrics_repo
from edrak.metrics.registry import get_registry

logger = logging.getLogger(__name__)


def _names(request: BusinessRequest) -> list[str]:
    ctx = request.business_context
    return [n for n in [request.company_profile.name, *[c.name for c in ctx.competitors], *ctx.markets] if n]


def _signals(cross_signal: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in (cross_signal or {}).get("signals") or []:
        out.append({"signal_id": s.get("signal_id"), "title": s.get("title"), "signal": s.get("signal"),
                    "interpretation": s.get("interpretation"), "implications": s.get("implications") or [],
                    "domains": s.get("domains_involved") or [], "supporting_findings": [
                        e.get("finding_id") for e in s.get("supporting_findings") or [] if isinstance(e, dict)],
                    "decision_question": s.get("decision_question"), "confidence": s.get("confidence")})
    return [s for s in out if s["signal_id"]]


def _finding_refs(results: list[WorkerResult], numbers: dict[tuple[str, int, str], int]) -> dict[str, list[int]]:
    refs: dict[str, list[int]] = {}
    for wr in results:
        for f in wr.findings:
            refs[f.finding_id] = sorted({n for r in f.evidence_refs if (n := numbers.get((wr.task_id, wr.attempt, r.evidence_id)))})
    return refs


async def build_v3(db: aiosqlite.Connection, request: BusinessRequest, results: list[WorkerResult], cross_signal: Optional[dict[str, Any]],
                   numbers: dict[tuple[str, int, str], int], *, lang: str = "en", goal: str = "") -> dict[str, Any]:
    """Everything the dashboard shows that comes from typed metrics. Raises nothing the caller must handle."""
    reg = get_registry()
    rid = request.request_id
    template = reg.template(request.business_context.use_case.value)
    rows = await metrics_repo.list_metrics(db, rid, include_unverified=True)
    conflicts = await metrics_repo.list_conflicts(db, rid)
    outcomes = await metrics_repo.list_outcomes(db, rid)
    trend_series = [s for wr in results if wr.worker.value == "customer_trends" for s in (wr.metadata.get("trend_series") or [])]

    cards, gaps = build_cards(rows, conflicts, outcomes, trend_series, template, request.company_profile.name)
    usable = {(r["metric_id"], r["entity"].lower()) for r in rows if r["status"] in ("verified", "thin") and not r["derived"]}
    strip = kpi_strip(cards, template)
    cov = coverage(outcomes, usable, template)

    signals = _signals(cross_signal)
    refs = _finding_refs(results, numbers)
    for s in signals:
        s["refs"] = sorted({n for fid in s.pop("supporting_findings") for n in refs.get(fid, [])})

    metrics_by_uid = {r["uid"]: r for r in rows}
    names = _names(request)
    copy = propose_copy(cards, signals, strip, framing=template.headline_framing, goal=goal or request.goal, names=names)
    headline, _rejected = apply_copy(copy, cards, signals, metrics_by_uid, names)
    if headline is None and copy is not None:  # one more try before falling back
        copy = propose_copy(cards, signals, strip, framing=template.headline_framing, goal=goal or request.goal, names=names)
        headline, _ = apply_copy(copy, cards, signals, metrics_by_uid, names)
    if headline is None:
        headline = next((s["title"] for s in signals if s.get("title")), None) or request.goal

    evidence_by_lens = {wr.worker.value: len(wr.evidence) for wr in results}
    secs, collapsed = sections(cards, evidence_by_lens, template, lang)
    for s in strip:  # the strip's fact string was for the prompt only
        s.pop("facts", None)

    return {
        "headline": headline,
        "kpis": strip,
        "coverage": cov,
        "gaps_panel": {
            "gaps": [{k: g[k] for k in ("card_key", "metric_id", "entity", "title", "blocking")} | {"reason": g["slots"]["reason"], "explanation": g["slots"]["explanation"]} for g in gaps],
            "collapsed_lenses": collapsed,
        },
        "sections": secs,
        "cards": {c["card_key"]: c for c in cards},
        "metrics": {r["uid"]: {k: r[k] for k in ("uid", "metric_id", "entity", "value", "value_text", "unit", "period", "label", "status",
                                                "scope", "sample_size", "derived", "formula", "source_span", "display_no", "inputs", "worker")}
                    for r in rows if r["status"] != "unverified"},
        "conflicts": conflicts,
        "signals_view": signals,
        "template": {"use_case": template.use_case, "kpi_strip": template.kpi_strip, "required": template.required},
    }
