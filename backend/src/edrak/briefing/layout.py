"""Section order, KPI strip and coverage. Deterministic."""

from __future__ import annotations

from typing import Any

from edrak.metrics.registry import UseCaseTemplate, get_registry

LENS_LABELS = {
    "competitor_intelligence": {"en": "Competitors", "ar": "المنافسون"},
    "customer_trends": {"en": "Customers and trends", "ar": "العملاء والاتجاهات"},
    "market_intelligence": {"en": "Market", "ar": "السوق"},
    "internal_intelligence": {"en": "Your company", "ar": "شركتك"},
}


def kpi_strip(cards: list[dict[str, Any]], template: UseCaseTemplate) -> list[dict[str, Any]]:
    """One slot per template metric, in template order, filled with the best card for it."""
    out = []
    reg = get_registry()
    for metric_id in template.kpi_strip:
        defn = reg.get(metric_id)
        mine = [c for c in cards if c.get("metric_id") == metric_id and c["component"] != "gap"]
        if not defn or not mine:
            continue
        card = sorted(mine, key=lambda c: {"verified": 0, "thin": 1, "conflict": 2}.get(c["status"], 3))[0]
        slots = card["slots"]
        value = slots.get("value")
        if value is None and slots.get("rows"):
            value = slots["rows"][0]["value"]
        if value is None and slots.get("parts"):
            value = f"{slots['parts'][0]['label']} {slots['parts'][0]['display']}"
        if value is None and slots.get("points"):
            value = slots["points"][-1]["display"]
        if value is None:
            continue
        out.append({"card_key": card["card_key"], "metric_id": metric_id, "label": defn.name, "entity": card.get("entity") or "",
                    "value": value, "status": card["status"], "comparator": slots.get("comparator"),
                    "uid": (card["metric_uids"] or [None])[0], "facts": _facts_short(card)})
    return out


def _facts_short(card: dict[str, Any]) -> str:
    comp = (card["slots"] or {}).get("comparator")
    return f"{comp['label']}: {'higher' if comp['direction'] > 0 else 'lower' if comp['direction'] < 0 else 'same'}" if comp else f"status={card['status']}"


def coverage(outcomes: list[dict[str, Any]], usable_keys: set[tuple[str, str]], template: UseCaseTemplate) -> dict[str, Any]:
    """Share of required figures that are verified. A figure counts only if it survived reconciliation."""
    required = [o for o in outcomes if o.get("required")]
    pairs = {(o["metric_id"], o["entity"].lower()) for o in required}
    items = [{"metric_id": m, "entity": e, "met": (m, e) in usable_keys} for m, e in sorted(pairs)]
    if not items:  # nothing was planned per entity: judge by the template's required metrics
        items = [{"metric_id": m, "entity": "", "met": any(k[0] == m for k in usable_keys)} for m in template.required]
    met = sum(1 for i in items if i["met"])
    return {"score": round(met / len(items), 3) if items else None, "met": met, "required": len(items), "items": items}


def sections(cards: list[dict[str, Any]], evidence_by_lens: dict[str, int], template: UseCaseTemplate, lang: str = "en") -> tuple[list[dict[str, Any]], list[str]]:
    """Fixed order from the template. A lens with too little evidence collapses into the gaps panel."""
    out, collapsed = [], []
    for lens in template.lens_order:
        keys = [c["card_key"] for c in cards if c["section"] == lens and c["component"] != "gap"]
        gap_keys = [c["card_key"] for c in cards if c["section"] == lens and c["component"] == "gap"]
        n = evidence_by_lens.get(lens, 0)
        is_collapsed = n < template.collapse_below_evidence
        if is_collapsed:
            collapsed.append(lens)
        if not keys and not gap_keys:
            continue
        out.append({"lens": lens, "label": LENS_LABELS.get(lens, {}).get(lang) or lens, "weight": template.lens_weights.get(lens, 0.5),
                    "cards": keys, "gap_cards": gap_keys, "evidence_count": n, "collapsed": is_collapsed})
    return out, collapsed
