"""What changed between two runs, deterministically.

Findings are matched by category and statement similarity. A finding with no close match in the
previous run is "added"; a close match whose wording or numbers differ is "changed"; a previous
finding with no match now is "removed". Importance: pricing, risk and positioning changes, and any
change in a number, are high; product and market signals medium; the rest low.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Optional

import aiosqlite

from edrak.db.util import fetch_all, new_id

SAME = 0.82        # above: the same claim
RELATED = 0.55     # between: the same claim, changed
HIGH = {"pricing_packaging", "risk", "positioning", "gap"}
MEDIUM = {"product_feature", "market_signal", "customer_sentiment", "opportunity"}
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


async def _findings(db: aiosqlite.Connection, request_id: str) -> list[dict[str, Any]]:
    """The last attempt's findings of each task, with the uid of the first cited evidence."""
    return await fetch_all(
        db,
        """SELECT f.statement, f.category, f.task_id,
                  (SELECT e.uid FROM finding_evidence fe JOIN evidence e ON e.task_id = fe.task_id AND e.attempt = fe.attempt
                     AND e.evidence_id = fe.evidence_id WHERE fe.task_id = f.task_id AND fe.attempt = f.attempt
                     AND fe.finding_id = f.finding_id LIMIT 1) AS evidence_uid
           FROM findings f
           WHERE f.request_id = ? AND f.attempt = (SELECT max(attempt) FROM findings g WHERE g.task_id = f.task_id)""",
        (request_id,),
    )


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def importance(category: str, numbers_changed: bool) -> str:
    if numbers_changed or category in HIGH:
        return "high"
    return "medium" if category in MEDIUM else "low"


async def diff_runs(db: aiosqlite.Connection, *, previous_request_id: str, request_id: str) -> list[dict[str, Any]]:
    old, new = await _findings(db, previous_request_id), await _findings(db, request_id)
    changes: list[dict[str, Any]] = []
    matched_old: set[int] = set()
    for f in new:
        best, best_i = 0.0, None
        for i, o in enumerate(old):
            if o["category"] != f["category"] or i in matched_old:
                continue
            r = _ratio(f["statement"], o["statement"])
            if r > best:
                best, best_i = r, i
        if best_i is not None and best >= RELATED:
            matched_old.add(best_i)
            if best < SAME or _NUM.findall(f["statement"]) != _NUM.findall(old[best_i]["statement"]):
                moved = _NUM.findall(f["statement"]) != _NUM.findall(old[best_i]["statement"])
                changes.append({"change_type": "changed", "category": f["category"], "title": f["statement"][:300],
                                "detail": f"Before: {old[best_i]['statement'][:300]}",
                                "importance": importance(f["category"], moved), "evidence_uid": f["evidence_uid"]})
        else:
            changes.append({"change_type": "added", "category": f["category"], "title": f["statement"][:300],
                            "detail": None, "importance": importance(f["category"], False), "evidence_uid": f["evidence_uid"]})
    for i, o in enumerate(old):
        if i not in matched_old:
            changes.append({"change_type": "removed", "category": o["category"], "title": o["statement"][:300],
                            "detail": "No longer found in this run.", "importance": "low", "evidence_uid": None})
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(changes, key=lambda c: order[c["importance"]])


async def record_changes(
    db: aiosqlite.Connection, *, workspace_id: str, monitor: Optional[dict[str, Any]], request_id: str,
    previous_request_id: str, brief_id: Optional[str],
) -> int:
    """Store change events and raise alerts at or above the monitor's minimum importance."""
    from edrak.db.repositories.notifications import notify

    changes = await diff_runs(db, previous_request_id=previous_request_id, request_id=request_id)
    minimum = {"low": 0, "medium": 1, "high": 2}[(monitor or {}).get("notify_min_importance", "medium")]
    level = {"low": 0, "medium": 1, "high": 2}
    alerts = 0
    for c in changes[:60]:
        cid = new_id()
        await db.execute(
            """INSERT INTO change_events (id, workspace_id, monitor_id, request_id, previous_request_id, change_type, importance, title, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, workspace_id, (monitor or {}).get("id"), request_id, previous_request_id, c["change_type"],
             c["importance"], c["title"], c["detail"]),
        )
        if c["evidence_uid"]:
            await db.execute("INSERT OR IGNORE INTO change_event_evidence (change_event_id, evidence_uid) VALUES (?, ?)", (cid, c["evidence_uid"]))
        if c["change_type"] != "removed" and level[c["importance"]] >= minimum:
            aid = new_id()
            await db.execute(
                """INSERT INTO alerts (id, workspace_id, monitor_id, change_event_id, brief_id, kind, importance, title, what_changed, why_it_matters)
                   VALUES (?, ?, ?, ?, ?, 'competitor_change', ?, ?, ?, ?)""",
                (aid, workspace_id, (monitor or {}).get("id"), cid, brief_id, c["importance"], c["title"][:200],
                 c["detail"] or c["title"], f"A {c['category'].replace('_', ' ')} finding {c['change_type']} since the last run."),
            )
            if c["evidence_uid"]:
                await db.execute("INSERT OR IGNORE INTO alert_evidence (alert_id, evidence_uid) VALUES (?, ?)", (aid, c["evidence_uid"]))
            if c["importance"] == "high" and (monitor or {}).get("notify_in_app", 1):
                await notify(db, workspace_id=workspace_id, kind="alert", title=c["title"][:120], alert_id=aid, commit=False)
            alerts += 1
    await db.commit()
    return alerts
