"""Derived metrics, computed in code with Decimal. Every one carries its formula and input ids."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import aiosqlite

from edrak.db.repositories import metrics as metrics_repo
from edrak.metrics.contracts import MetricObservation, MetricScope
from edrak.metrics.registry import get_registry

# A competitor's figure is compared with the company's own. Some metrics use a different id for "ours".
OWN_ID = {"price_per_tier": "own_price"}
VS_SUFFIX = "__vs_company"


def pct_delta(theirs: float, ours: float) -> Optional[Decimal]:
    if ours == 0:
        return None
    t, o = Decimal(str(theirs)), Decimal(str(ours))
    return ((t - o) / o * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


async def derive(db: aiosqlite.Connection, request_id: str, company: str) -> int:
    """Replace this run's derived rows. Returns how many were written."""
    await metrics_repo.clear_for_request(db, request_id, methods=("derived",))
    rows = await metrics_repo.list_metrics(db, request_id)
    rows = [r for r in rows if not r["derived"] and r["value"] is not None and r["metric_id"]]
    reg = get_registry()
    ours = {(r["metric_id"], (r["period"] or "").lower()): r for r in rows if r["entity"].lower() == company.lower()}
    written = 0
    for r in rows:
        defn = reg.get(r["metric_id"])
        if not defn or defn.comparator != "company" or r["entity"].lower() == company.lower():
            continue
        base_id = OWN_ID.get(r["metric_id"], r["metric_id"])
        # Only like with like: the same period and the same unit. A different period is never compared.
        base = ours.get((base_id, (r["period"] or "").lower()))
        if not base or r["unit"] != base["unit"]:
            continue
        delta = pct_delta(r["value"], base["value"])
        if delta is None:
            continue
        both_ok = r["status"] == "verified" and base["status"] == "verified"
        obs = MetricObservation(
            metric_id=r["metric_id"] + VS_SUFFIX, entity=r["entity"], value_text=f"{delta:+}%", value=float(delta),
            unit="%", period=r["period"], definition=f"{defn.label()} compared with {company}",
            evidence_id="derived", source_span=f"({r['value_text']} − {base['value_text']}) ÷ {base['value_text']} × 100",
            scope=MetricScope(r["scope"]), derived=True,
            formula=f"({r['entity']} {r['value_text']} − {company} {base['value_text']}) ÷ {company} {base['value_text']} × 100",
        )
        uid = await metrics_repo.insert_metric(
            db, request_id, obs, status="verified" if both_ok else "thin", method="derived",
            inputs=[r["uid"], base["uid"]], commit=False)
        await db.execute("UPDATE metrics SET evidence_uid = ? WHERE uid = ?", (r["evidence_uid"], uid))
        written += 1
    await db.commit()
    return written


async def record_conflicts(db: aiosqlite.Connection, request_id: str) -> int:
    """Write one conflict row per (metric, entity, period) group whose rows disagree."""
    await db.execute("DELETE FROM metric_conflicts WHERE request_id = ?", (request_id,))
    rows = [r for r in await metrics_repo.list_metrics(db, request_id) if r["status"] == "conflict" and r["value"] is not None]
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["metric_id"], r["entity"].lower(), (r["period"] or "").lower(), (r["label"] or "").lower()), []).append(r)
    for (metric_id, entity, period, _), members in groups.items():
        await metrics_repo.write_conflict(
            db, request_id, metric_id, members[0]["entity"], members[0]["period"],
            min(m["value"] for m in members), max(m["value"] for m in members), [m["uid"] for m in members])
    return len(groups)
