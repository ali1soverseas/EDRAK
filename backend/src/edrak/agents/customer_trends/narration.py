"""Turn the worker's structured events into one readable line each, for the live run screen.

The worker already emits an event at every step. This only words them. It decides nothing
and changes no behaviour: an event that reads as noise returns None and is not shown.
"""

from __future__ import annotations

from typing import Any

# What each graph node is doing, in plain words.
_NODE_TEXT = {
    "intake": "reading the task",
    "plan_queries": "planning searches",
    "join": "combining what the sources returned",
    "analyze": "analysing the collected posts and reviews",
    "gap_check": "checking what is still missing",
    "write_findings": "writing findings",
    "submit": "checking and saving findings",
}

_COLLECTION_BRANCHES = {"demand", "reviews", "social", "trends", "competitors"}


def _short(text: Any, limit: int = 160) -> str:
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def narrate(event: dict[str, Any]) -> str | None:
    kind = event.get("type")
    if kind == "node_started":
        node = str(event.get("node", ""))
        if node in _NODE_TEXT:
            return _NODE_TEXT[node].capitalize()
        if node in _COLLECTION_BRANCHES:
            return f"Collecting from the {node} branch"
        return None
    if kind == "node_finished" and event.get("status") == "error":
        return f"Step {event.get('node')} stopped with {event.get('error')}"
    if kind == "tool_called":
        tool = str(event.get("tool", "tool"))
        provider = event.get("provider")
        where = f" via {provider}" if provider else ""
        status = str(event.get("status", ""))
        if status in ("ok", "partial"):
            count = event.get("count")
            note = " (partial)" if status == "partial" else ""
            return f"{tool}{where}: {count} item(s){note}" if count is not None else f"{tool}{where}: ok{note}"
        message = event.get("message")
        error_code = event.get("error_code")
        reason = (
            f"{message} [{error_code}]"
            if message and error_code
            else message or error_code or status
        )
        fallback = ", then tried a fallback" if event.get("fallback_used") else ""
        return f"{tool}{where} failed ({_short(reason, 180)}){fallback}"
    if kind == "gap_found":
        description = event.get("description")
        return f"Gap found: {_short(description)}" if description else "Gap found"
    if kind == "replan":
        gaps = event.get("gap_ids") or []
        return f"Planning another search pass for {len(gaps)} open gap(s)"
    if kind == "finding_accepted":
        return "Finding accepted"
    if kind == "finding_rejected":
        reasons = event.get("reasons") or []
        return f"Finding rejected: {_short(reasons[0], 100)}" if reasons else "Finding rejected"
    if kind == "run_failed":
        return f"Stopped: {_short(event.get('message') or event.get('error'))}"
    if kind == "run_finished":
        return f"Finished: {event.get('findings', 0)} findings from {event.get('evidence', 0)} sources"
    return None
