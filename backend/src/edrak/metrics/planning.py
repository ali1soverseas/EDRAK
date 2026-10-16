"""Turn a use-case template into per-worker metric checklists. Pure code, no model."""

from __future__ import annotations

from edrak.metrics.contracts import MetricRequirement
from edrak.metrics.registry import MetricDef, get_registry

MAX_ENTITIES_PER_METRIC = 5

# Metrics that describe one company: asked once for our company, not per competitor.
_OWN = {"own_price", "win_loss", "capacity", "roadmap_items"}
# Customer metrics worth asking for every named competitor too.
_PER_COMPETITOR_CUSTOMER = {"share_of_voice", "sentiment_split", "rating", "top_pain_points", "switching_intent"}


def _entities(defn: MetricDef, company: str, competitors: list[str], markets: list[str]) -> list[str]:
    if defn.id in _OWN or defn.owner == "internal_intelligence":
        return [company]
    if defn.owner == "competitor_intelligence":
        return competitors[:MAX_ENTITIES_PER_METRIC]
    if defn.owner == "market_intelligence":
        return markets[:MAX_ENTITIES_PER_METRIC] or ["target market"]
    if defn.owner == "customer_trends":
        names = [company]
        if defn.id in _PER_COMPETITOR_CUSTOMER:
            names += competitors[:3]
        return names
    return [company]


def requirements_for(worker: str, use_case: str, company: str, competitors: list[str], markets: list[str],
                     offering: str = "") -> list[MetricRequirement]:
    """The checklist one worker owns for this use case. Required metrics come first."""
    reg = get_registry()
    tpl = reg.template(use_case)
    required = set(tpl.required)
    out: list[MetricRequirement] = []
    for metric_id in [*tpl.required, *tpl.optional]:
        defn = reg.get(metric_id)
        if not defn or defn.owner != worker:
            continue
        for entity in _entities(defn, company, competitors, markets):
            out.append(MetricRequirement(
                metric_id=defn.id, entity=entity, required=defn.id in required, definition=defn.definition,
                preferred_source_types=list(defn.preferred_sources),
                query_hints=[h.replace("{entity}", entity).replace("{market}", entity).replace("{offering}", offering or "")
                             .replace("  ", " ").strip() for h in defn.query_hints],
                check_rule=", ".join(f"{k}={v}" for k, v in defn.check.items()) or None,
            ))
    out.sort(key=lambda r: not r.required)
    return out
