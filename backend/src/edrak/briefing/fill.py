"""Fill and validate cards from stored metrics. Deterministic.

The component for a metric is fixed by the registry (the same metric type always gets the same
component). Values come from the metrics table by uid. A card that fails its checks falls back to a
table with a caveat; a required metric with no verified value becomes a gap card. Never an empty
card, a zero or a guessed value.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from edrak.briefing.derive import VS_SUFFIX
from edrak.metrics.format import format_value
from edrak.metrics.registry import MetricDef, UseCaseTemplate, get_registry

PARTS_TOLERANCE = 1.0
DEFAULT_MIN_POINTS = 3


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "x"


def _ref(r: dict[str, Any]) -> Optional[int]:
    return r.get("display_no")


def _uids(rows: list[dict[str, Any]]) -> list[str]:
    return [r["uid"] for r in rows]


def _refs(rows: list[dict[str, Any]]) -> list[int]:
    return sorted({n for n in (_ref(r) for r in rows) if n})


def _worst(rows: list[dict[str, Any]]) -> str:
    order = ["conflict", "thin", "verified"]
    return min((r["status"] for r in rows), key=lambda s: order.index(s) if s in order else 0)


def _card(defn: MetricDef, entity: str, component: str, rows: list[dict[str, Any]], slots: dict[str, Any], *,
          status: Optional[str] = None, caveat: Optional[str] = None, title: Optional[str] = None, key: Optional[str] = None) -> dict[str, Any]:
    return {
        "card_key": key or f"{defn.id}:{slug(entity)}", "section": defn.owner, "component": component,
        "metric_id": defn.id, "entity": entity, "title": title or f"{defn.label()} · {entity}" if entity else defn.label(),
        "status": status or _worst(rows), "slots": slots, "caveat": caveat, "refs": _refs(rows), "metric_uids": _uids(rows),
        "derived": any(r.get("derived") for r in rows), "why_visual": None, "headline": None,
    }


def _table(defn: MetricDef, entity: str, rows: list[dict[str, Any]], caveat: str, *, key: Optional[str] = None) -> dict[str, Any]:
    body = [{"entity": r["entity"], "label": r.get("label"), "period": r.get("period"), "value": format_value(r["value"], r["unit"]),
             "uid": r["uid"], "refs": _refs([r])} for r in rows]
    return _card(defn, entity, "table", rows, {"rows": body}, caveat=caveat, key=key)


def _comparators(rows: list[dict[str, Any]], derived: dict[tuple, dict[str, Any]], row: dict[str, Any], defn: MetricDef) -> Optional[dict[str, Any]]:
    d = derived.get((row["metric_id"], row["entity"].lower()))
    if d and defn.comparator == "company":
        return {"kind": "company", "label": "vs us", "value": format_value(d["value"], "%"), "direction": 1 if d["value"] > 0 else -1 if d["value"] < 0 else 0,
                "uid": d["uid"], "formula": d["formula"]}
    return None


def build_cards(rows: list[dict[str, Any]], conflicts: list[dict[str, Any]], outcomes: list[dict[str, Any]],
                trend_series: list[dict[str, Any]], template: UseCaseTemplate, company: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (cards, gaps). Gap cards are included in `cards` and listed again in `gaps` for the panel."""
    reg = get_registry()
    usable = [r for r in rows if r["status"] in ("verified", "thin", "conflict") and r["metric_id"] and r["value"] is not None]
    derived = {(r["metric_id"].replace(VS_SUFFIX, ""), r["entity"].lower()): r for r in usable if r["derived"]}
    base = [r for r in usable if not r["derived"] and reg.get(r["metric_id"])]
    by_metric: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in base:
        by_metric[r["metric_id"]].append(r)
    conflict_by = {(c["metric_id"], c["entity"].lower()): c for c in conflicts}

    cards: list[dict[str, Any]] = []
    for metric_id, group in by_metric.items():
        defn = reg.get(metric_id)
        visual = defn.visual
        if visual == "kpi":
            by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in group:
                by_entity[r["entity"]].append(r)
            for entity, ers in by_entity.items():
                ers.sort(key=lambda r: (r["period"] or ""), reverse=True)
                head = ers[0]
                same = [r for r in ers if (r["period"] or "") == (head["period"] or "")]
                slots: dict[str, Any] = {"value": format_value(head["value"], head["unit"]), "uid": head["uid"], "period": head["period"],
                                         "comparator": _comparators(rows, derived, head, defn)}
                if len(ers) > len(same):  # an earlier period exists: show change against it, computed from stored values
                    prev = next(r for r in ers if (r["period"] or "") != (head["period"] or ""))
                    if prev["value"]:
                        slots["comparator"] = slots["comparator"] or {"kind": "prior", "label": f"vs {prev['period'] or 'prior'}",
                            "value": format_value(round((head["value"] - prev["value"]) / prev["value"] * 100, 1), "%"),
                            "direction": 1 if head["value"] > prev["value"] else -1, "uid": prev["uid"],
                            "formula": f"({head['value_text']} − {prev['value_text']}) ÷ {prev['value_text']} × 100"}
                status = _worst(same)
                c = conflict_by.get((metric_id, entity.lower()))
                if status == "conflict" and c:
                    slots["range"] = {"low": format_value(c["low"], head["unit"]), "high": format_value(c["high"], head["unit"]),
                                      "sources": [{"uid": r["uid"], "value": format_value(r["value"], r["unit"]), "refs": _refs([r])} for r in same]}
                cards.append(_card(defn, entity, "kpi", same, slots, status=status))
        elif visual == "ladder":
            by_row: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
            for r in group:
                by_row[(r["entity"], r["label"] or "", r["period"] or "")].append(r)
            rows_out = []
            for members in by_row.values():
                head = max(members, key=lambda r: r["value"])
                row = {"label": (head["label"] or head["entity"]), "entity": head["entity"], "value": format_value(head["value"], head["unit"]),
                       "raw": head["value"], "uid": head["uid"], "refs": _refs(members), "status": _worst(members),
                       "comparator": _comparators(rows, derived, head, defn)}
                if len({m["value"] for m in members}) > 1:  # sources disagree: show the range and each source, not a pick
                    lo = min(members, key=lambda r: r["value"])
                    row["range"] = {"low": format_value(lo["value"], lo["unit"]), "high": format_value(head["value"], head["unit"]),
                                    "sources": [{"uid": m["uid"], "value": format_value(m["value"], m["unit"]), "refs": _refs([m])} for m in members]}
                    row["value"] = f"{row['range']['low']}–{row['range']['high']}"
                    row["status"] = "conflict"
                rows_out.append(row)
            rows_out.sort(key=lambda r: r["raw"], reverse=True)
            cards.append(_card(defn, "", "ladder", group, {"rows": rows_out}, key=defn.id))
        elif visual == "segmented":
            cards += _segmented(defn, group, conflicts)
        elif visual == "line":
            cards += _lines(defn, group)
        elif visual == "timeline":
            ordered = sorted(group, key=lambda r: (r["period"] or "", r["entity"]))
            slots = {"events": [{"when": r["period"], "label": r["label"] or r["entity"], "value": format_value(r["value"], r["unit"]),
                                 "uid": r["uid"], "refs": _refs([r])} for r in ordered]}
            cards.append(_card(defn, "", "timeline", group, slots, key=defn.id))
        else:
            slots = {"items": [{"entity": r["entity"], "text": r["label"] or r["value_text"], "uid": r["uid"], "refs": _refs([r])} for r in group]}
            cards.append(_card(defn, "", "text", group, slots, key=defn.id))

    cards += _series_cards(trend_series, reg.get("search_interest"))
    gaps = gap_cards(cards, usable, outcomes, template)
    return cards + [g for g in gaps if g["blocking"]], gaps


def _segmented(defn: MetricDef, group: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    has_label = any(r["label"] for r in group)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in group:
        buckets[r["entity"] if has_label else ""].append(r)
    need = defn.check.get("min_sample")
    for entity, rs in buckets.items():
        total = sum(r["value"] for r in rs)
        small = need and any((r["sample_size"] or 0) < need for r in rs)
        parts_ok = (not defn.check.get("parts_sum_100")) or abs(total - 100) <= PARTS_TOLERANCE
        if small:
            out.append(_table(defn, entity, rs, f"Not shown as a chart: fewer than {need} items behind it.", key=f"{defn.id}:{slug(entity)}" if entity else defn.id))
        elif not parts_ok or len(rs) < 2:
            out.append(_table(defn, entity, rs, "Not shown as a chart: the parts are incomplete or do not add up to 100%.",
                              key=f"{defn.id}:{slug(entity)}" if entity else defn.id))
        else:
            parts = [{"label": r["label"] or r["entity"], "value": r["value"], "display": format_value(r["value"], r["unit"]),
                      "uid": r["uid"], "refs": _refs([r])} for r in sorted(rs, key=lambda r: -r["value"])]
            slots = {"parts": parts, "n": min((r["sample_size"] for r in rs if r["sample_size"]), default=None)}
            out.append(_card(defn, entity, "segmented", rs, slots, key=f"{defn.id}:{slug(entity)}" if entity else defn.id))
    return out


def _lines(defn: MetricDef, group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    need = defn.check.get("min_points", DEFAULT_MIN_POINTS)
    out = []
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in group:
        if r["period"]:
            by_entity[r["entity"]].append(r)
    for entity, rs in by_entity.items():
        rs.sort(key=lambda r: r["period"])
        if len({r["period"] for r in rs}) < need:
            out.append(_table(defn, entity, rs, f"Not shown as a chart: fewer than {need} data points."))
            continue
        pts = [{"x": r["period"], "y": r["value"], "display": format_value(r["value"], r["unit"]), "uid": r["uid"]} for r in rs]
        out.append(_card(defn, entity, "line", rs, {"points": pts, "unit": rs[0]["unit"]}))
    return out


def _series_cards(series: list[dict[str, Any]], defn: Optional[MetricDef]) -> list[dict[str, Any]]:
    if not defn:
        return []
    need = defn.check.get("min_points", DEFAULT_MIN_POINTS)
    out = []
    for s in series:
        pts = s.get("points") or []
        if len(pts) < need:
            continue
        c = _card(defn, s.get("keyword") or "", "line", [], {"points": [{"x": x, "y": y, "display": f"{y:g}"} for x, y in pts], "unit": None,
                                                                "data_source": {"name": s.get("source"), "geo": s.get("geo"), "timeframe": s.get("timeframe")}},
                  status="verified", key=f"{defn.id}:{slug(s.get('keyword') or '')}")
        c["title"] = f"{defn.label()} · {s.get('keyword')}"
        out.append(c)
    return out


def gap_cards(cards: list[dict[str, Any]], usable: list[dict[str, Any]], outcomes: list[dict[str, Any]], template: UseCaseTemplate) -> list[dict[str, Any]]:
    """Missing figures. Required ones are blocking and render as gap cards; optional ones only appear in the gaps panel."""
    reg = get_registry()
    have = {(r["metric_id"], r["entity"].lower()) for r in usable if not r["derived"]}
    have_metric = {m for m, _ in have} | {c["metric_id"] for c in cards if c["component"] == "line"}
    gaps, seen = [], set()
    for o in outcomes:
        defn = reg.get(o["metric_id"])
        if not defn or (o["metric_id"], o["entity"].lower()) in have or (o["metric_id"], o["entity"].lower()) in seen:
            continue
        if o["status"] == "found":  # the checklist passed but nothing survived reconciliation
            reason = "unverified"
        else:
            reason = o.get("reason") or "not_found"
        seen.add((o["metric_id"], o["entity"].lower()))
        gaps.append(_gap(defn, o["entity"], reason, bool(o.get("required"))))
    for mid in template.required:
        defn = reg.get(mid)
        if defn and mid not in have_metric and not any(g["metric_id"] == mid for g in gaps):
            gaps.append(_gap(defn, "", "not_found", True))
    return gaps


REASONS = {
    "not_found": "Searched and not found.",
    "not_publicly_disclosed": "Not publicly disclosed.",
    "only_third_party_source": "Only third-party sources; this figure needs the company's own page.",
    "below_min_sample": "Too little data behind it to show safely.",
    "conflict": "Sources disagree and none is authoritative.",
    "unverified": "Found, but the number could not be matched to its saved source.",
}


def _gap(defn: MetricDef, entity: str, reason: str, blocking: bool) -> dict[str, Any]:
    return {
        "card_key": f"gap:{defn.id}:{slug(entity)}", "section": defn.owner, "component": "gap", "metric_id": defn.id, "entity": entity,
        "title": f"{defn.label()} · {entity}" if entity else defn.label(), "status": "gap", "blocking": blocking,
        "slots": {"reason": reason, "explanation": REASONS.get(reason, reason), "definition": defn.definition},
        "refs": [], "metric_uids": [], "derived": False, "caveat": None, "why_visual": None, "headline": None,
    }
