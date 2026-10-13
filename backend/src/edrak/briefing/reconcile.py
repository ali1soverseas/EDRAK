"""Verify and reconcile typed metrics. Deterministic: no model, no network.

1. Each number must appear in its saved source, or it stays `unverified` and never reaches a card.
2. The same (metric, entity, period, part) reported with different values by different sources is a
   `conflict`: the brief shows the range and both sources instead of picking one.
3. A figure backed by only one third-party source is `thin`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

from edrak.contracts import Evidence, WorkerResult
from edrak.metrics.contracts import MetricObservation, MetricScope, MetricStatus
from edrak.metrics.numbers import appears_in, span_in_source
from edrak.metrics.registry import get_registry

CONFLICT_TOLERANCE = 0.01  # values within 1% of each other agree


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").lower().split())


def _source_text(e: Evidence) -> str:
    return f"{e.extracted_fact or ''}\n{e.excerpt or ''}\n{e.page_text or ''}"


def _host(e: Evidence) -> str:
    return (urlparse(e.source_url or "").hostname or e.publisher or e.evidence_id).lower()


def group_key(o: MetricObservation) -> tuple:
    return (o.metric_id, _norm(o.entity), _norm(o.period), _norm(o.label))


def _agree(a: float, b: float) -> bool:
    top = max(abs(a), abs(b))
    return top == 0 or abs(a - b) / top <= CONFLICT_TOLERANCE


def reconcile(results: list[WorkerResult]) -> tuple[list[WorkerResult], list[str]]:
    """Return the results with every observation's status set, and a sentence per conflict."""
    reg = get_registry()
    evidence = {(r.task_id, e.evidence_id): e for r in results for e in r.evidence}
    parsed: dict[str, list[MetricObservation]] = {}
    groups: dict[tuple, list[tuple[str, MetricObservation, Evidence]]] = defaultdict(list)

    for r in results:
        out: list[MetricObservation] = []
        for raw in r.metadata.get("metrics") or []:
            try:
                o = MetricObservation.model_validate(raw)
            except Exception:  # noqa: BLE001
                continue
            e = evidence.get((r.task_id, o.evidence_id))
            ok = e is not None and span_in_source(o.source_span, _source_text(e)) and appears_in(o.value_text, _source_text(e))
            if not ok or o.value is None:
                out.append(o.model_copy(update={"status": MetricStatus.UNVERIFIED}))
                continue
            out.append(o)
            groups[group_key(o)].append((r.task_id, o, e))
        parsed[r.task_id] = out

    status: dict[int, MetricStatus] = {}
    conflicts: list[str] = []
    for key, members in groups.items():
        values = [o.value for _, o, _ in members]
        defn = reg.get(key[0])
        name = defn.label() if defn else (key[0] or "figure")
        distinct = [m for i, m in enumerate(members) if all(not _agree(m[1].value, n[1].value) for n in members[:i])]
        disagree = any(not _agree(a, b) for i, a in enumerate(values) for b in values[i + 1:])
        sources = {_host(e) for _, _, e in members}
        for _, o, _ in members:
            if disagree and len(sources) > 1:
                status[id(o)] = MetricStatus.CONFLICT
            elif (o.scope in (MetricScope.OFFICIAL, MetricScope.INTERNAL, MetricScope.PUBLIC_PROXY) or len(sources) > 1
                  or (o.formula and o.sample_size)):  # counted in code from labelled items
                status[id(o)] = MetricStatus.VERIFIED
            else:
                status[id(o)] = MetricStatus.THIN
            if defn and defn.check.get("min_sample") and (o.sample_size or 0) < defn.check["min_sample"] and o.sample_size is not None:
                status[id(o)] = MetricStatus.THIN
        if disagree and len(sources) > 1:
            lo, hi = min(members, key=lambda m: m[1].value)[1], max(members, key=lambda m: m[1].value)[1]
            conflicts.append(f"{name} for {key[1] or 'the company'}: {lo.value_text} to {hi.value_text} across {len(sources)} sources")

    fixed = []
    for r in results:
        # Recomputed on every pass: after a re-plan, untouched workers' figures must not keep a stale status.
        mets = [o.model_copy(update={"status": status.get(id(o), MetricStatus.UNVERIFIED)}) for o in parsed[r.task_id]]
        meta = dict(r.metadata)
        meta["metrics"] = [m.model_dump(mode="json") for m in mets]
        fixed.append(r.model_copy(update={"metadata": meta}))
    return fixed, conflicts
