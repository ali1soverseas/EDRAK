"""Runs monitors when they are due. An asyncio task started with the app; no extra services.

Every minute it picks the active monitors whose next_run_at has passed, drafts an analysis from the
monitor (its use case, goal, tracked entities and focus), and either approves the plan itself (when
the monitor allows auto-approval) or leaves it waiting for the person, with a notification. When a
monitor run finishes, run_manager calls after_monitor_run to diff it against the previous run.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from edrak.db.connection import get_db
from edrak.db.util import fetch_all, fetch_one, now_iso

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
FOCUS_KEYS = {"pricing": "pricing", "features": "features", "announcements": "positioning", "customer_perception": "sentiment"}


def next_run(monitor: dict[str, Any], after: Optional[datetime] = None) -> Optional[str]:
    """The next due time (UTC ISO) from the monitor's frequency, day and time in its time zone."""
    if monitor["frequency"] == "event_only" or not monitor.get("run_time"):
        return None
    tz = ZoneInfo(monitor.get("time_zone") or "Africa/Cairo")
    now = (after or datetime.now(tz)).astimezone(tz)
    hour, minute = (int(x) for x in monitor["run_time"].split(":")[:2])
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if monitor["frequency"] == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
    elif monitor["frequency"] == "weekly":
        target = int(monitor.get("day_of_week") or 1)       # ISO, Monday = 1
        candidate += timedelta(days=(target - candidate.isoweekday()) % 7)
        if candidate <= now:
            candidate += timedelta(days=7)
    else:  # monthly, on the same day of month as the first run
        if candidate <= now:
            month = candidate.month % 12 + 1
            candidate = candidate.replace(year=candidate.year + (candidate.month == 12), month=month, day=min(candidate.day, 28))
    return candidate.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def launch(monitor_id: str, *, manual: bool = False) -> Optional[str]:
    """Draft (and, if allowed, start) one monitor run. Returns the analysis id."""
    from edrak.api.analysis_service import TOGGLE_TO_SOURCE_TYPES
    from edrak.api.routers.analyses import draft
    from edrak.api.run_manager import run_manager
    from edrak.api.schemas import AnalysisForm
    from edrak.db.repositories import analyses as analyses_repo
    from edrak.db.repositories.notifications import notify

    async with get_db() as db:
        m = await fetch_one(db, "SELECT * FROM monitors WHERE id = ?", (monitor_id,))
        if not m:
            return None
        ents = await fetch_all(db, """SELECT e.name, e.kind FROM monitor_entities me JOIN entities e ON e.id = me.entity_id
                                      WHERE me.monitor_id = ?""", (monitor_id,))
        owner = await fetch_one(db, "SELECT owner_id FROM workspaces WHERE id = ?", (m["workspace_id"],))
        from edrak.db.util import loads

        focus = [FOCUS_KEYS[f] for f in loads(m["focus"], []) if f in FOCUS_KEYS]
        form = AnalysisForm(
            use_case=m["use_case"],
            goal=m.get("goal") or f"What changed for {', '.join(e['name'] for e in ents) or 'the tracked companies'} since the last run?",
            competitors=[e["name"] for e in ents if e["kind"] == "competitor"],
            market=", ".join(e["name"] for e in ents if e["kind"] == "market"),
            focus=focus,
            time_window_days=7 if m["frequency"] in ("daily", "weekly") else 31,
            sources={k: True for k in TOGGLE_TO_SOURCE_TYPES},
        )
        row = await draft(db, form=form, workspace_id=m["workspace_id"], user_id=owner["owner_id"] if owner else None)
        rid = row["request_id"]
        await db.execute(
            "UPDATE business_requests SET launched_by = 'monitor', monitor_id = ?, trigger = 'scheduled', title = ? WHERE request_id = ?",
            (monitor_id, f"{m['name']}: {datetime.now().strftime('%d %b')}", rid),
        )
        extras = loads((await fetch_one(db, "SELECT extras FROM business_requests WHERE request_id = ?", (rid,)))["extras"], {})
        if m["auto_approve_plans"]:
            extras["auto_review"] = True
        from edrak.db.util import dumps

        await db.execute("UPDATE business_requests SET extras = ? WHERE request_id = ?", (dumps(extras), rid))
        await db.execute("UPDATE monitors SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                         (now_iso(), next_run(m) if not manual else m["next_run_at"], monitor_id))
        await db.commit()
        if m["auto_approve_plans"]:
            plan = await analyses_repo.current_plan(db, rid)
            if plan and await analyses_repo.approve_plan(db, plan["plan_id"], user_id=None, auto=True):
                await run_manager.start_run(rid, user_id=None, approver_name="the monitor", auto=True)
        else:
            await notify(db, workspace_id=m["workspace_id"], kind="plan_awaiting_approval",
                         title=f"{m['name']}: a plan is waiting for approval", request_id=rid)
        return rid


async def after_monitor_run(request_id: str, brief_id: Optional[str]) -> None:
    """Compare a finished monitor run with the monitor's previous one; raise alerts."""
    from edrak.monitoring.diff import record_changes

    async with get_db() as db:
        r = await fetch_one(db, "SELECT workspace_id, monitor_id, created_at FROM business_requests WHERE request_id = ?", (request_id,))
        if not r or not r["monitor_id"]:
            return
        prev = await fetch_one(
            db, """SELECT r.request_id FROM business_requests r JOIN runs u ON u.request_id = r.request_id
                   WHERE r.monitor_id = ? AND r.request_id <> ? AND u.status IN ('completed','partial') AND r.created_at < ?
                   ORDER BY r.created_at DESC LIMIT 1""",
            (r["monitor_id"], request_id, r["created_at"]),
        )
        if not prev:
            return
        monitor = await fetch_one(db, "SELECT * FROM monitors WHERE id = ?", (r["monitor_id"],))
        await record_changes(db, workspace_id=r["workspace_id"], monitor=monitor, request_id=request_id,
                             previous_request_id=prev["request_id"], brief_id=brief_id)


async def tick() -> None:
    async with get_db() as db:
        due = await fetch_all(db, "SELECT id FROM monitors WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= ?", (now_iso(),))
    for m in due:
        try:
            await launch(m["id"])
        except Exception:  # noqa: BLE001 - one monitor must not stop the others
            logger.exception("Monitor %s failed to launch", m["id"])


async def run_forever() -> None:
    while True:
        try:
            await tick()
        except Exception:  # noqa: BLE001
            logger.exception("Monitor scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)
