"""Figures the planner proposes for a field the fixed metric sets do not cover (onboarding industry "Other").

The planner names up to MAX_PROPOSED figures, each with a definition, a unit, the worker that owns it and a
search phrase. Code validates them, turns each into an ordinary registry metric with an `x_` id, and saves the
definitions on the request (`extras["custom_metrics"]`) and on the brief (`template.custom_metrics`). Everything
downstream (the metrics agent, reconciliation, cards, the deck, challenges) then treats them like any other
metric. They sit on top of the shared core of the `dynamic` set, so a run never depends on them alone.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from edrak.metrics.profile import DYNAMIC, business_model
from edrak.metrics.registry import MetricDef, get_registry

logger = logging.getLogger(__name__)

MAX_PROPOSED = 5
_UNITS = {"currency", "percent", "count", "quantity", "date"}  # an index means nothing as one number, so none is proposed
_OWNERS = {"competitor_intelligence", "market_intelligence", "customer_trends", "internal_intelligence"}
_VISUAL = {"currency": "kpi", "percent": "kpi", "count": "kpi", "quantity": "kpi", "date": "timeline"}
_COMPARATOR = {"competitor_intelligence": "company"}


class ProposedMetric(BaseModel):
    name: str = Field(description="Short name, e.g. 'Hotel occupancy rate'.")
    definition: str = Field(description="What exactly is counted, in one sentence.")
    unit_kind: str = Field(description="One of: currency, percent, count, quantity, date.")
    owner: str = Field(description="One of: competitor_intelligence, market_intelligence, customer_trends, internal_intelligence.")
    search_phrase: str = Field(description="2 to 6 words to search for it, without the company or place.")
    reads_as: str = Field(default="", description="One sentence: what the figure tells the decision maker.")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:40]


def to_defs(proposed: list[Any]) -> list[MetricDef]:
    """Valid proposals as registry metrics. A proposal with an unknown unit or owner, or a duplicate, is dropped."""
    reg = get_registry()
    out: list[MetricDef] = []
    seen: set[str] = set()
    for raw in proposed or []:
        try:
            p = raw if isinstance(raw, ProposedMetric) else ProposedMetric.model_validate(raw)
        except Exception:  # noqa: BLE001
            continue
        unit, owner = p.unit_kind.strip().lower(), p.owner.strip().lower()
        mid = "x_" + _slug(p.name)
        if unit not in _UNITS or owner not in _OWNERS or len(mid) < 4 or mid in seen:
            continue
        if reg.match(p.name):  # the registry already has this figure under its own id
            continue
        phrase = " ".join(p.search_phrase.split()[:8])
        hints = {"competitor_intelligence": [f"{{entity}} {phrase}"], "market_intelligence": [f"{{market}} {phrase}"],
                 "customer_trends": [f"{{entity}} {phrase}"], "internal_intelligence": [phrase]}[owner]
        seen.add(mid)
        out.append(MetricDef(
            id=mid, name={"en": p.name.strip()[:60]}, aliases=[phrase.lower()], definition=p.definition.strip()[:300],
            reads_as=p.reads_as.strip()[:200], unit_kind=unit, period="year", owner=owner,
            preferred_sources=["internal"] if owner == "internal_intelligence" else ["official", "third_party"],
            query_hints=hints, check={}, visual=_VISUAL[unit], comparator=_COMPARATOR.get(owner, "none"),
        ))
        if len(out) >= MAX_PROPOSED:
            break
    return out


def register(defs: list[Any]) -> list[str]:
    """Add proposed metrics to the registry (idempotent). Returns their ids in order."""
    reg = get_registry()
    ids = []
    for d in defs or []:
        try:
            m = d if isinstance(d, MetricDef) else MetricDef.model_validate(d)
        except Exception:  # noqa: BLE001
            continue
        if not m.id.startswith("x_"):
            continue
        reg.metrics.setdefault(m.id, m)
        ids.append(m.id)
    return ids


def metric_set(request: Any) -> tuple[Optional[str], list[str]]:
    """(metric set, proposed metric ids) for a request, registering its proposed metrics first."""
    ctx = request.business_context
    model = business_model(request.company_profile, offering=ctx.offering, goal=request.goal)
    custom = register((request.extras or {}).get("custom_metrics") or []) if model == DYNAMIC else []
    return model, custom


def from_document(doc: dict[str, Any]) -> list[str]:
    """Register the proposed metrics a saved brief used. Returns their ids."""
    return register(((doc.get("template") or {}).get("custom_metrics")) or [])
