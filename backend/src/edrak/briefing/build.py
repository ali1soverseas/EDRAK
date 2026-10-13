"""Assemble the typed half of a brief (document version 3) from stored metrics."""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiosqlite

from edrak.briefing.fill import build_cards
from edrak.briefing.layout import coverage, kpi_strip, sections
from edrak.briefing.propose import apply_copy, overlap, propose_copy, unmet_dimensions
from edrak.metrics.format import format_value
from edrak.contracts import BusinessRequest, WorkerResult
from edrak.db.repositories import metrics as metrics_repo
from edrak.metrics.custom import metric_set
from edrak.metrics.registry import get_registry

logger = logging.getLogger(__name__)


def _names(request: BusinessRequest) -> list[str]:
    ctx = request.business_context
    return [n for n in [request.company_profile.name, *[c.name for c in ctx.competitors], *ctx.markets] if n]


def _signals(cross_signal: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in (cross_signal or {}).get("signals") or []:
        out.append({"signal_id": s.get("signal_id"), "type": s.get("signal_type"), "urgency": s.get("urgency"),
                    "decision_relevance": s.get("decision_relevance"), "evidence_gaps": s.get("evidence_gaps") or [],
                    "title": s.get("title"), "signal": s.get("signal"),
                    "interpretation": s.get("interpretation"), "implications": s.get("implications") or [],
                    "domains": s.get("domains_involved") or [], "supporting_findings": [
                        e.get("finding_id") for e in s.get("supporting_findings") or [] if isinstance(e, dict)],
                    "decision_question": s.get("decision_question"), "confidence": s.get("confidence")})
    return [s for s in out if s["signal_id"]]


DUPLICATE = 0.7  # title overlap above which two signals say the same thing


def rank_signals(signals: list[dict[str, Any]], template: Any) -> list[dict[str, Any]]:
    """Order by the use case's lens emphasis, then confidence; drop near-duplicates."""
    for s in signals:
        s["emphasis"] = round(max((template.emphasis(d) for d in s.get("domains") or []), default=0.5), 2)
    ordered = sorted(signals, key=lambda s: (-(s.get("type") == "conflict"), -s["emphasis"], -(s.get("confidence") or 0)))
    kept: list[dict[str, Any]] = []
    for s in ordered:
        if all(overlap(s.get("title") or "", k.get("title") or "") < DUPLICATE for k in kept):
            kept.append(s)
    return kept


def conflict_signals(conflicts: list[dict[str, Any]], rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """A figure the sources disagree on is a signal in its own right, with both sides shown. Built in code."""
    from edrak.metrics.registry import get_registry

    reg = get_registry()
    out = []
    for c in conflicts:
        members = [rows[u] for u in c.get("metric_uids") or [] if u in rows]
        if len(members) < 2:
            continue
        defn = reg.get(c.get("metric_id"))
        name = defn.label() if defn else (c.get("metric_id") or "A figure")
        unit = members[0].get("unit")
        sides = sorted(members, key=lambda r: r["value"])
        out.append({
            "signal_id": f"conflict:{c.get('metric_id')}:{(c.get('entity') or '').lower()}", "type": "conflict",
            "title": f"Sources disagree on {name.lower()} for {c.get('entity')}",
            "signal": f"Reported from {format_value(c['low'], unit)} to {format_value(c['high'], unit)}"
                      + (f" for {c['period']}" if c.get("period") else "") + ". Neither source is authoritative, so no single value is used.",
            "interpretation": None, "implications": [], "domains": [defn.owner] if defn else [],
            "sides": [{"value": format_value(r["value"], r["unit"]), "uid": r["uid"], "ref": r.get("display_no")} for r in sides],
            "refs": sorted({r["display_no"] for r in sides if r.get("display_no")}), "confidence": None,
            "decision_question": None, "supporting_findings": [],
        })
    return out


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
    ctx = request.business_context
    model, custom = metric_set(request)
    template = reg.template(ctx.use_case.value, model, custom)
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
    signals = rank_signals([*conflict_signals(conflicts, metrics_by_uid), *signals], template)

    names = _names(request)
    found_ids = {m for m, _ in usable}
    unmet = unmet_dimensions(found_ids, template.required, signals)
    narrative = [s for s in signals if s.get("type") != "conflict"]
    kw = dict(framing=template.headline_framing, goal=goal or request.goal, names=names, unmet=unmet)
    copy = propose_copy(cards, narrative, strip, **kw)
    headline, _rejected = apply_copy(copy, cards, narrative, metrics_by_uid, names, unmet)
    if headline is None and copy is not None:  # one more try, told why, before falling back
        copy = propose_copy(cards, narrative, strip, **kw, feedback=(
            "Your last headline was rejected. Use only the key figures and signals given, name a real company or market, "
            f"type no digit, and say nothing about: {', '.join(unmet) or 'anything not listed'}."))
        headline, _ = apply_copy(copy, cards, narrative, metrics_by_uid, names, unmet)
    if headline is None:
        headline = next((s["title"] for s in narrative if s.get("title")), None) or request.goal

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
        "unmet_dimensions": unmet,
        "template": {"use_case": template.use_case, "key": template.key, "custom": custom,
                     "custom_metrics": [reg.get(m).model_dump(mode="json") for m in custom if reg.get(m)], "kpi_strip": template.kpi_strip, "required": template.required,
                     "metrics_theme": template.metrics_theme, "signal_emphasis": template.signal_emphasis,
                     "decision_framing": template.decision_framing},
    }
