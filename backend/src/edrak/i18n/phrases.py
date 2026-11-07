"""Fixed sentences the interface already knows how to say in Arabic.

The English here is what gets stored. The screen looks up the same key in its
dictionary and does not send the sentence to a translation model.
"""

from __future__ import annotations

import re
from typing import Any

_WORKERS = {
    "internal intelligence": "internal_intelligence",
    "competitor intelligence": "competitor_intelligence",
    "market intelligence": "market_intelligence",
    "customer trends": "customer_trends",
}

_CHOICES = {
    "continue to synthesis": "continue",
    "send workers out again": "replan",
    "stop and write the brief": "stop",
}

_FIXED_REASON = {
    "No matching evidence was found.": "run.noEvidenceNone",
    "The worker stopped without a result.": "run.failedStopped",
}

CHAT_ERRORS = {
    "Ask Edrak cannot reach the language model right now. Try again in a moment.": "chat.modelUnavailable",
    "Ask Edrak could not answer right now.": "chat.couldNotAnswer",
}

_PLAN_BY = re.compile(r"Plan approved by (.+)\. Launching (\d+) worker task(s)?\.$")
_PLAN_AUTO = re.compile(r"Plan approved automatically \(monitor setting\)\. Launching (\d+) worker task(s)?\.$")
_QUEUED = re.compile(r"Queued for (.+)$")
_FOUND_GAP = re.compile(r"Found (\d+) findings across (\d+) sources\. Gap: (.+)$")
_FOUND = re.compile(r"Found (\d+) findings across (\d+) sources\.$")
_COMPLETE = re.compile(r"Complete\. Found (\d+) findings across (\d+) sources\.(?: Gap reported: (.+))?$")
_NO_EVIDENCE = re.compile(r"No evidence found\. (.+)$")
_FAILED = re.compile(r"Failed: (.+)$")
_STOPPED = re.compile(r"The run stopped: (.+)$")
_CHECKPOINT = re.compile(
    r"Checkpoint 2: verification is done \((\d+) of (\d+) findings verified\)\. The run waits for your review\.$"
)
_CHOSE = re.compile(r"(.+) chose to (.+?)\.(?: Note: (.+))?$")
_VERIFY = re.compile(r"Verification completed: (\d+) findings evaluated \((.+)\)\.$")
_CONTINUE = re.compile(r"Continue without the (.+) worker\. Verification and the brief will mark the evidence as partial\.$")
_REPLAN = re.compile(r"The Supervisor proposes sending (.+) out again\. You decide at the review\.$")
_BRIEF_READY = re.compile(r"Brief (\d+) is ready: (.+)$")
_REVIEW_FOUND = re.compile(r"Review what was found: (.+)$")
_PLAN_WAITING = re.compile(r"(.+): a plan is waiting for approval$")
_ALERT_WHY = re.compile(r"A (.+) finding (added|changed|removed) since the last run\.$")
_WARNINGS: list[tuple[re.Pattern[str], str, list[str]]] = [
    (re.compile(r"(\d+) link\(s\) were ignored because they were not valid\.$"), "import.linksIgnored", ["n"]),
    (re.compile(r"(\d+) page\(s\) were skipped because the website's robots\.txt does not allow them\.$"), "import.robotsSkipped", ["n"]),
    (re.compile(r"(\d+) page\(s\) could not be read\.$"), "import.pagesUnreadable", ["n"]),
    (re.compile(r"(\d+) page\(s\) had no readable text\.$"), "import.pagesBlank", ["n"]),
    (re.compile(r"The sources were longer than the size limit, so some text was cut\.$"), "import.cut", []),
    (re.compile(r"(\d+) lower-priority source\(s\) did not fit and were not read\.$"), "import.omitted", ["n"]),
    (re.compile(r"(\d+) source\(s\) had no readable text and were left out\.$"), "import.emptySources", ["n"]),
    (re.compile(r"(\d+) source\(s\) were mostly instruction-like text and were left out\.$"), "import.riskySources", ["n"]),
    (re.compile(r"(\d+) instruction-like line\(s\) were removed from the sources\.$"), "import.removedLines", ["n"]),
    (re.compile(r"No readable text was found in this PDF\.$"), "import.noPdfText", []),
    (re.compile(r"The Arabic text on page (\d+) may be in reverse order\.$"), "import.arabicReversed", ["page"]),
    (re.compile(r"The document has (\d+) pages; only the first (\d+) were read\.$"), "import.pdfTruncated", ["pages", "read"]),
    (re.compile(r"Headings and tables could not be read, so plain text was used\.$"), "import.plainText", []),
    (re.compile(r"No text could be read from the scanned page\(s\) (.+)\.$"), "import.scanUnreadable", ["pages"]),
    (re.compile(r"There was no readable text to build a profile from\.$"), "import.noProfileText", []),
    (re.compile(r"Only (\d+) characters of readable text were found, so the draft is likely incomplete\.$"), "import.shortText", ["n"]),
    (re.compile(r"KPI '(.+)' has no target yet\.$"), "plan.kpiNoTarget", ["name"]),
    (re.compile(r"KPI '(.+)' has no stored baseline; mark it as a new measure or run more research\.$"), "plan.kpiNoBaseline", ["name"]),
    (re.compile(r"Milestone '(.+)' is well after the last action ends\.$"), "plan.milestoneLate", ["title"]),
    (re.compile(r"Milestone '(.+)' has no go / no-go criteria\.$"), "plan.milestoneNoCriteria", ["title"]),
    (re.compile(r"The plan has no KPIs\.$"), "plan.noKpis", []),
    (re.compile(r"The plan has no risk register\.$"), "plan.noRisks", []),
]


def _worker(label: str) -> dict[str, str]:
    return {"worker": label, "worker_id": _WORKERS.get(label, "")}


def _count_key(base: str, n: int) -> str:
    return f"{base}.one" if n == 1 else f"{base}.other"


def classify_message(message: str) -> tuple[str, dict[str, Any]] | None:
    """A stored run line that has a dictionary sentence, or None when the line is generated."""
    text = message.strip()
    if not text:
        return None
    if text == "The brief is ready.":
        return "run.briefReady", {}
    if text == "Run resumed.":
        return "run.resumed", {}
    if text == "Pause requested. The run stops after the current step.":
        return "run.pauseRequested", {}
    if text == "The run is paused. Resume it to continue from this step.":
        return "run.paused", {}
    if text == "All workers have reported. Verification is checking completeness, source quality and contradictions.":
        return "run.allReported", {}
    if text == "Cross-domain signals identified. Preparing the decision analysis.":
        return "run.signalsReady", {}
    if text == "Decision analysis written. Building the brief.":
        return "run.analysisWritten", {}
    if text == "Verification suggests more research.":
        return "run.verificationSuggests", {}
    if text == "Task queued":
        return "run.taskQueued", {}
    if text == "Worker dispatched":
        return "run.workerDispatched", {}
    if text == "Starting verification":
        return "run.verificationStarting", {}
    match = _QUEUED.fullmatch(text)
    if match:
        return "run.queued", _worker(match.group(1))
    match = _PLAN_AUTO.fullmatch(text)
    if match:
        n = int(match.group(1))
        return _count_key("run.planApproved.auto", n), {"n": n}
    match = _PLAN_BY.fullmatch(text)
    if match:
        n = int(match.group(2))
        return _count_key("run.planApproved.by", n), {"name": match.group(1), "n": n}
    match = _COMPLETE.fullmatch(text)
    if match:
        params: dict[str, Any] = {"findings": int(match.group(1)), "sources": int(match.group(2))}
        if match.group(3):
            params["gap"] = match.group(3)
            return "run.completeGap", params
        return "run.complete", params
    match = _FOUND_GAP.fullmatch(text)
    if match:
        return "run.foundGap", {"findings": int(match.group(1)), "sources": int(match.group(2)), "gap": match.group(3)}
    match = _FOUND.fullmatch(text)
    if match:
        return "run.found", {"findings": int(match.group(1)), "sources": int(match.group(2))}
    match = _NO_EVIDENCE.fullmatch(text)
    if match:
        reason = match.group(1)
        fixed = _FIXED_REASON.get(reason)
        if fixed == "run.noEvidenceNone":
            return fixed, {}
        return "run.noEvidence", {"reason": reason}
    match = _FAILED.fullmatch(text)
    if match:
        reason = match.group(1)
        if reason in _FIXED_REASON:
            return "run.failedStopped", {}
        return "run.failed", {"reason": reason}
    match = _STOPPED.fullmatch(text)
    if match:
        return "run.stopped", {"reason": match.group(1)}
    match = _CHECKPOINT.fullmatch(text)
    if match:
        return "run.checkpoint", {"verified": int(match.group(1)), "total": int(match.group(2))}
    match = _CHOSE.fullmatch(text)
    if match and match.group(2) in _CHOICES:
        decision = _CHOICES[match.group(2)]
        params = {"name": match.group(1)}
        if match.group(3):
            params["note"] = match.group(3)
            return f"run.chose.{decision}.note", params
        return f"run.chose.{decision}", params
    match = _VERIFY.fullmatch(text)
    if match:
        return "run.verificationDone", {"n": int(match.group(1)), "status": match.group(2), "status_code": match.group(2).replace(" ", "_")}
    return None


def classify_callout(text: str) -> tuple[str, dict[str, Any]] | None:
    match = _CONTINUE.fullmatch(text.strip())
    if match:
        return "run.continueWithout", _worker(match.group(1))
    match = _REPLAN.fullmatch(text.strip())
    if match:
        return "run.replanPropose", {"workers": match.group(1)}
    return None


def classify_notification(title: str, body: str | None) -> dict[str, Any]:
    found: dict[str, Any] = {}
    match = _BRIEF_READY.fullmatch(title.strip())
    if match:
        found["title_key"] = "notice.briefReady"
        found["title_params"] = {"no": match.group(1), "title": match.group(2)}
    match = _REVIEW_FOUND.fullmatch(title.strip())
    if match:
        found["title_key"] = "notice.reviewFound"
        found["title_params"] = {"title": match.group(1)}
    match = _PLAN_WAITING.fullmatch(title.strip())
    if match:
        found["title_key"] = "notice.planWaiting"
        found["title_params"] = {"name": match.group(1)}
    if body == "Verified.":
        found["body_key"] = "notice.verified"
        found["body_params"] = {}
    elif body == "Verified with gaps marked.":
        found["body_key"] = "notice.verifiedGaps"
        found["body_params"] = {}
    return found


def classify_alert(row: dict[str, Any]) -> None:
    """Attach dictionary keys for the shells around a finding. The finding itself stays prose."""
    why = str(row.get("why_it_matters") or "")
    match = _ALERT_WHY.fullmatch(why)
    if match:
        row["why_key"] = "alert.sinceLastRun"
        row["why_params"] = {"category": match.group(1), "change": match.group(2)}
    what = str(row.get("what_changed") or "")
    if what == "No longer found in this run.":
        row["what_key"] = "alert.noLongerFound"
        row["what_params"] = {}
    elif what.startswith("Before: "):
        row["what_key"] = "alert.before"
        row["what_params"] = {"statement": what[len("Before: "):]}


def classify_warning(message: str) -> dict[str, Any]:
    text = message.strip()
    for pattern, key, names in _WARNINGS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        return {"message": message, "key": key, "params": {name: match.group(index) for index, name in enumerate(names, start=1)}}
    return {"message": message, "key": None, "params": {}}


def annotate_run(view: dict[str, Any]) -> dict[str, Any]:
    """Add message keys to a run snapshot. Generated lines are marked so only they are translated."""
    activity = view.get("verification_activity") or ""
    found = classify_message(activity) if activity else None
    if found:
        view["verification_activity_key"] = found[0]
        view["verification_activity_params"] = found[1]
    for task in view.get("tasks") or []:
        found = classify_message(task.get("activity") or "")
        if found:
            task["activity_key"] = found[0]
            task["activity_params"] = found[1]
        for entry in task.get("log") or []:
            found = classify_message(entry.get("text") or "")
            if found:
                entry["key"] = found[0]
                entry["text_key"] = found[0]
                entry["params"] = found[1]
    for entry in view.get("verification_log") or []:
        found = classify_message(entry.get("text") or "")
        if found:
            entry["key"] = found[0]
            entry["params"] = found[1]
    for event in view.get("events") or []:
        if not event.get("message_key"):
            found = classify_message(event.get("message") or "")
            if found:
                event["message_key"], event["message_params"] = found
        callout = event.get("callout")
        if isinstance(callout, dict) and callout.get("text") and not callout.get("text_key"):
            found = classify_callout(callout["text"])
            if found:
                callout["text_key"], callout["text_params"] = found
        if event.get("kind") == "replan" and not event.get("message_key"):
            event["generated"] = True
    return view
