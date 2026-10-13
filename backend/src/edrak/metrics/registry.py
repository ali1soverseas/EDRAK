"""Loads metrics/registry.yaml and metrics/templates.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

_DIR = Path(__file__).parent


class MetricDef(BaseModel):
    id: str
    name: dict[str, str]
    aliases: list[str] = Field(default_factory=list)
    definition: str
    # One line telling a reader what the figure means for the decision ("How big the prize is in this market").
    reads_as: str = ""
    unit_kind: str
    period: str
    owner: str
    preferred_sources: list[str] = Field(default_factory=list)
    query_hints: list[str] = Field(default_factory=list)
    check: dict = Field(default_factory=dict)
    visual: str
    comparator: str = "none"

    def label(self, lang: str = "en") -> str:
        return self.name.get(lang) or self.name.get("en") or self.id


class UseCaseTemplate(BaseModel):
    use_case: str
    kpi_strip: list[str]
    required: list[str]
    optional: list[str] = Field(default_factory=list)
    lens_order: list[str]
    lens_weights: dict[str, float] = Field(default_factory=dict)
    collapse_below_evidence: int = 2
    headline_framing: str
    metrics_theme: dict[str, str] = Field(default_factory=lambda: {"title": "Business metrics", "subtitle": ""})
    signal_emphasis: dict[str, float] = Field(default_factory=dict)
    decision_framing: str = ""
    # Other metric sets for other kinds of business, e.g. hardware_infrastructure: {kpi_strip, required, optional}.
    variants: dict[str, dict] = Field(default_factory=dict)
    # The use case, plus "@<business model>" when a variant was applied.
    key: str = ""

    def emphasis(self, lens: str) -> float:
        return self.signal_emphasis.get(lens, self.lens_weights.get(lens, 0.5))


class Registry:
    def __init__(self, metrics: list[MetricDef], templates: dict[str, UseCaseTemplate]) -> None:
        self.metrics = {m.id: m for m in metrics}
        self.templates = templates

    def get(self, metric_id: Optional[str]) -> Optional[MetricDef]:
        return self.metrics.get(metric_id or "")

    def template(self, use_case: str, business_model: Optional[str] = None, custom: Optional[list[str]] = None) -> UseCaseTemplate:
        """The use case's template, with the metric set for the kind of business when one is defined.

        `use_case` may also be a saved key such as "market_entry_expansion@hardware_infrastructure". `custom` are the
        ids of figures the planner proposed (metrics.custom): they lead the strip and the first two are required.
        """
        use_case, _, saved = (use_case or "").partition("@")
        base = self.templates.get(use_case) or self.templates["competitive_intelligence"]
        model = business_model or saved
        variant = base.variants.get(model or "")
        if not variant:
            return base.model_copy(update={"key": base.use_case})
        tpl = base.model_copy(update={**variant, "key": f"{base.use_case}@{model}"})
        extra = [m for m in custom or [] if m in self.metrics and m not in tpl.kpi_strip]
        if extra:
            tpl = tpl.model_copy(update={"kpi_strip": [*extra, *tpl.kpi_strip], "required": [*tpl.required, *extra[:2]],
                                         "optional": [*tpl.optional, *extra[2:]]})
        return tpl

    def owned_by(self, worker: str) -> list[MetricDef]:
        return [m for m in self.metrics.values() if m.owner == worker]

    def match(self, label: str) -> Optional[MetricDef]:
        """Match free text to a metric by id, name or alias (case-insensitive)."""
        key = (label or "").strip().lower().replace(" ", "_")
        if key in self.metrics:
            return self.metrics[key]
        low = (label or "").strip().lower()
        for m in self.metrics.values():
            names = [m.name.get("en", ""), m.name.get("ar", ""), *m.aliases]
            if low and any(low == n.lower() for n in names if n):
                return m
        return None

    def ids(self) -> list[str]:
        return list(self.metrics)


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    raw = yaml.safe_load((_DIR / "registry.yaml").read_text(encoding="utf-8"))
    metrics = [MetricDef(**m) for m in raw["metrics"]]
    tpl_raw = yaml.safe_load((_DIR / "templates.yaml").read_text(encoding="utf-8"))["templates"]
    templates = {k: UseCaseTemplate(use_case=k, **v) for k, v in tpl_raw.items()}
    reg = Registry(metrics, templates)
    for t in templates.values():
        ids = [*t.kpi_strip, *t.required, *t.optional]
        ids += [m for v in t.variants.values() for k in ("kpi_strip", "required", "optional") for m in v.get(k, [])]
        for mid in ids:
            if mid not in reg.metrics:
                raise ValueError(f"template {t.use_case} references unknown metric {mid}")
    return reg
