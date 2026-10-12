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


class Registry:
    def __init__(self, metrics: list[MetricDef], templates: dict[str, UseCaseTemplate]) -> None:
        self.metrics = {m.id: m for m in metrics}
        self.templates = templates

    def get(self, metric_id: Optional[str]) -> Optional[MetricDef]:
        return self.metrics.get(metric_id or "")

    def template(self, use_case: str) -> UseCaseTemplate:
        return self.templates.get(use_case) or self.templates["competitive_intelligence"]

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
        for mid in [*t.kpi_strip, *t.required, *t.optional]:
            if mid not in reg.metrics:
                raise ValueError(f"template {t.use_case} references unknown metric {mid}")
    return reg
