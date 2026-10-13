"""The brief as five slides, in a fixed order. Built in code from the assembled brief document.

1. summary          the headline insight, key findings, top signals, coverage
2. business_metrics our figures against theirs (theme from the use-case template)
3. signals          cross-domain signals, with the lenses involved
4. decisions        distinct options; the action plan is generated after one is chosen
5. evidence_gaps    coverage per lens, source mix, conflicts, gaps, and anything collapsed

Slides only reference keys that already exist in the document (card keys, signal ids, evidence
numbers). Nothing here writes text a model produced or a number that is not in the metrics table.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from edrak.briefing.layout import LENS_LABELS
from edrak.metrics.registry import get_registry

DECK_VERSION = 1
SLIDES = ("summary", "business_metrics", "signals", "decisions", "evidence_gaps")
KEY_FINDINGS = 5
TOP_SIGNALS = 3
OWN_IDS = {"price_per_tier": "own_price"}


def _lens_label(lens: str) -> str:
    return LENS_LABELS.get(lens, {}).get("en") or lens.replace("_", " ")


def _key_findings(doc: dict[str, Any], template: Any) -> list[dict[str, Any]]:
    """Verified findings, the lenses this use case cares about first, then the best evidenced."""
    out = []
    for lens in doc.get("lenses") or []:
        for item in lens.get("findings") or []:
            if item.get("status") != "verified":
                continue
            out.append({"text": item["finding"]["statement"], "lens": lens["worker"], "finding_id": item["finding"]["finding_id"],
                        "refs": sorted({r["display_no"] for r in item.get("refs") or []}), "reliability": item.get("reliability", 1),
                        "weight": template.emphasis(lens["worker"])})
    out.sort(key=lambda f: (-f["weight"], -f["reliability"], -len(f["refs"])))
    return out[:KEY_FINDINGS]


def _comparison(doc: dict[str, Any], template: Any, company: str) -> list[dict[str, Any]]:
    """Our figure next to each competitor's, per KPI metric. Only stored values; mismatched periods are flagged."""
    reg = get_registry()
    metrics = list((doc.get("metrics") or {}).values())
    # Figures the brief already judged unreadable on their own (one search-index value, "shares" adding past 100%,
    # too few items) are gaps, so they are not listed here as numbers either.
    unreadable = {(g.get("metric_id"), (g.get("entity") or "").casefold()) for g in (doc.get("gaps_panel") or {}).get("gaps") or []
                  if g.get("reason") in ("inconsistent", "below_min_sample")}
    usable = [m for m in metrics if m.get("status") in ("verified", "thin", "conflict") and not m.get("derived") and m.get("metric_id")
              and (m["metric_id"], m["entity"].casefold()) not in unreadable]
    rows = []
    for metric_id in template.kpi_strip:
        defn = reg.get(metric_id)
        if not defn or metric_id.startswith("own_"):
            continue
        own_id = OWN_IDS.get(metric_id, metric_id)
        ours = [m for m in usable if m["metric_id"] == own_id and m["entity"].casefold() == company.casefold()]
        theirs = [m for m in usable if m["metric_id"] == metric_id and m["entity"].casefold() != company.casefold()]
        if not theirs and not ours:
            continue
        cell = lambda m: {"entity": m["entity"], "value": m["value_text"], "label": m.get("label"), "period": m.get("period"),
                          "uid": m["uid"], "ref": m.get("display_no"), "status": m["status"]}
        periods = {(m.get("period") or "").lower() for m in [*ours, *theirs]}
        rows.append({"metric_id": metric_id, "name": defn.name, "lens": defn.owner, "reads_as": defn.reads_as or None,
                     "definition": defn.definition, "ours": [cell(m) for m in ours], "theirs": [cell(m) for m in theirs],
                     "comparable": len(periods) <= 1,
                     "note": None if len(periods) <= 1 else "Periods differ, so these are listed side by side, not compared."})
    return rows


def _coverage_by_lens(doc: dict[str, Any], template: Any) -> list[dict[str, Any]]:
    reg = get_registry()
    items = (doc.get("coverage") or {}).get("items") or []
    out = []
    for lens in template.lens_order:
        block = next((l for l in doc.get("lenses") or [] if l["worker"] == lens), None)
        findings = block.get("findings") if block else []
        mine = [i for i in items if (reg.get(i["metric_id"]) and reg.get(i["metric_id"]).owner == lens)]
        out.append({
            "lens": lens, "label": _lens_label(lens), "sources": block.get("source_count", 0) if block else 0,
            "findings": len(findings or []), "verified": sum(1 for f in findings or [] if f.get("status") == "verified"),
            "required": len(mine), "met": sum(1 for i in mine if i.get("met")),
            "failed": bool(block and block.get("failed")),
            "collapsed": lens in ((doc.get("gaps_panel") or {}).get("collapsed_lenses") or []),
        })
    return out


def _source_mix(doc: dict[str, Any]) -> dict[str, Any]:
    ev = doc.get("evidence") or []
    origin = Counter(e.get("origin") or "external" for e in ev)
    reliability = Counter(int(e.get("reliability") or 1) for e in ev)
    notes = Counter(e.get("note") or "other" for e in ev)
    return {"total": len(ev), "internal": origin.get("internal", 0), "external": origin.get("external", 0),
            "by_reliability": {str(k): reliability.get(k, 0) for k in (3, 2, 1)}, "by_kind": dict(notes.most_common())}


def _gaps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for g in (doc.get("gaps_panel") or {}).get("gaps") or []:
        out.append({"kind": "figure", "title": g["title"], "reason": g.get("reason"), "explanation": g.get("explanation"),
                    "blocking": g.get("blocking", False), "card_key": g.get("card_key"), "metric_id": g.get("metric_id"),
                    "entity": g.get("entity"), "action": "provide_data" if g.get("reason") == "not_publicly_disclosed" else "rerun"})
    for g in doc.get("gaps") or []:
        out.append({"kind": "research", "title": g["text"], "reason": None, "explanation": f"Reported by {g.get('reported_by', '').replace('_', ' ')}.",
                    "blocking": False, "card_key": None, "action": "rerun", "lens": g.get("reported_by")})
    return out


def _priorities_known(doc: dict[str, Any]) -> bool:
    pr = (doc.get("decision_analysis") or {}).get("priorities") or []
    return any(p.get("impact") not in (None, "unknown") and p.get("feasibility") not in (None, "unknown") for p in pr)


def build_deck(doc: dict[str, Any]) -> dict[str, Any]:
    """The five slides. A metrics slide with nothing to show collapses into the gaps slide."""
    reg = get_registry()
    tpl_info = doc.get("template") or {}
    template = reg.template(tpl_info.get("key") or tpl_info.get("use_case") or str(doc.get("use_case") or "competitive_intelligence"))
    company = ((doc.get("request") or {}).get("company_profile") or {}).get("name") or ""
    cards = doc.get("cards") or {}
    signals = doc.get("signals_view") or []
    collapsed = set((doc.get("gaps_panel") or {}).get("collapsed_lenses") or [])

    kpi_keys = [k["card_key"] for k in doc.get("kpis") or []]
    metric_cards = [c for c in cards.values() if c["component"] != "gap" and c["section"] not in collapsed]
    lens_rank = {lens: i for i, lens in enumerate(template.lens_order)}
    metric_cards.sort(key=lambda c: (c["card_key"] not in kpi_keys, lens_rank.get(c["section"], 9),
                                     kpi_keys.index(c["card_key"]) if c["card_key"] in kpi_keys else 0))
    gap_cards = [c["card_key"] for c in cards.values() if c["component"] == "gap"]
    comparison = _comparison(doc, template, company)
    metrics_empty = not metric_cards and not comparison

    summary = {
        "id": "summary", "title": "Summary",
        "headline": doc.get("headline") or doc.get("summary_text") or doc.get("question"),
        "question": doc.get("question"), "use_case": template.use_case,
        "key_findings": _key_findings(doc, template),
        "top_signals": [s["signal_id"] for s in signals[:TOP_SIGNALS]],
        "kpis": kpi_keys[:4],
        "coverage": doc.get("coverage"),
        "limits": (doc.get("summary") or {}).get("limits"),
        "counts": doc.get("counts"),
        "unmet": doc.get("unmet_dimensions") or [],
    }
    business = {
        "id": "business_metrics", "title": template.metrics_theme.get("title") or "Business metrics",
        "subtitle": template.metrics_theme.get("subtitle") or "", "kpis": kpi_keys, "cards": [c["card_key"] for c in metric_cards],
        "gap_cards": gap_cards, "comparison": comparison, "company": company,
        "collapsed": metrics_empty, "collapsed_reason": "No figure could be found and checked for this question." if metrics_empty else None,
    }
    lenses = sorted({d for s in signals for d in s.get("domains") or []}, key=lambda d: lens_rank.get(d, 9))
    signals_slide = {
        "id": "signals", "title": "Signals", "items": [s["signal_id"] for s in signals],
        "lenses": [{"lens": l, "label": _lens_label(l)} for l in lenses],
        "emphasis": template.signal_emphasis or template.lens_weights,
    }
    decisions = {
        "id": "decisions", "title": "Decisions", "framing": template.decision_framing,
        "options": [o["key"] for o in doc.get("options") or []],
        "priorities_known": _priorities_known(doc),
    }
    evidence = {
        "id": "evidence_gaps", "title": "Evidence and gaps",
        "coverage": doc.get("coverage"), "by_lens": _coverage_by_lens(doc, template), "source_mix": _source_mix(doc),
        "conflicts": [s["signal_id"] for s in signals if s.get("type") == "conflict"],
        "gaps": _gaps(doc), "collapsed": sorted(collapsed) + (["business_metrics"] if metrics_empty else []),
    }
    return {"version": DECK_VERSION, "order": list(SLIDES), "slides": [summary, business, signals_slide, decisions, evidence]}
