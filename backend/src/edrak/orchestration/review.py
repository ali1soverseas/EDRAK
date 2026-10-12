"""Checkpoint 2: a person reviews what the Supervisor and verification concluded.

After verification the run stops (a LangGraph interrupt, with the state checkpointed) and shows
the person:

  * the verification decision and its summary,
  * how many findings were verified and how many are weak,
  * the information gaps and the conflicts,
  * the re-plan the Supervisor proposes, as editable tasks (only when re-planning is possible).

The person answers with one of:

  continue   go on to synthesis and the brief with what was found;
  replan     send workers out again, with the proposed tasks or tasks the person edited;
  stop       end the run now; the brief is written from what was found, marked partial.

The run resumes from the checkpoint with `Command(resume={...})`, also after a server restart.
A monitor whose plans are auto-approved also skips this checkpoint when verification passed.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from ..contracts import ResearchTask, WorkerType
from ..contracts.verification import FindingCheckStatus, VerificationStatus
from ..core.config import settings
from .nodes import _targeted_tasks
from .state import OrchestrationState

DECISIONS = ("continue", "replan", "stop")
# A person may ask for more rounds than the automatic limit, but not without end.
MAX_HUMAN_REPLANS = 3


def review_payload(state: OrchestrationState) -> dict[str, Any]:
    """What the person sees at the checkpoint. Plain JSON."""
    decision = state.get("verification")
    result = state.get("verification_result")
    verdicts = list(getattr(result, "findings", None) or [])
    verified = sum(1 for v in verdicts if v.verification_status is FindingCheckStatus.VERIFIED)
    summary = getattr(result, "control_summary", None)
    replans = state.get("replan_count") or 0
    proposed: list[dict[str, Any]] = []
    reasons: dict[str, list[str]] = {}
    targeted = _targeted_tasks(state) if replans < MAX_HUMAN_REPLANS else None
    if targeted:
        tasks, reasons = targeted
        proposed = [
            {"task_id": t.task_id, "worker": t.worker.value, "goal": t.goal, "focus": t.focus}
            for t in tasks
        ]
    elif decision is not None and decision.targeted_actions and replans < MAX_HUMAN_REPLANS:
        plan = state.get("plan")
        by_worker = {t.worker: t for t in (plan.tasks if plan else [])}
        for action in decision.targeted_actions:
            task = by_worker.get(action.worker)
            if task is not None and task.task_id not in {p["task_id"] for p in proposed}:
                proposed.append({
                    "task_id": task.task_id,
                    "worker": task.worker.value,
                    "goal": f"{task.goal} {action.reason}",
                    "focus": task.focus,
                })
    status = decision.status.value if decision is not None else "cannot_complete"
    recommended = "continue"
    if status in ("replan_required", "retry_required") and proposed and replans < settings.MAX_REPLANS:
        recommended = "replan"
    elif status == "cannot_complete" and verified == 0:
        recommended = "replan" if proposed else "stop"
    return {
        "verification_status": status,
        "summary": decision.summary if decision is not None else "Verification did not run.",
        "findings_total": len(verdicts),
        "findings_verified": verified,
        "findings_weak": len(verdicts) - verified,
        "missing_information": list(getattr(summary, "missing_information", []) or [])[:20],
        "conflicts": list(getattr(summary, "conflicts", []) or [])[:20],
        "failures": list(getattr(summary, "failures", []) or [])[:20],
        "proposed_tasks": proposed,
        "replan_reasons": reasons,
        "replans_done": replans,
        "can_replan": replans < MAX_HUMAN_REPLANS,
        "recommended": recommended,
    }


def _auto_decision(state: OrchestrationState) -> dict[str, Any] | None:
    """Monitor runs with auto-approval skip the checkpoint when verification passed."""
    request = state.get("request")
    decision = state.get("verification")
    if request is None or not request.extras.get("auto_review"):
        return None
    if decision is not None and decision.status is VerificationStatus.VERIFIED:
        return {"decision": "continue", "auto": True}
    return None


def _tasks_from(edits: list[dict[str, Any]], state: OrchestrationState) -> list[ResearchTask]:
    """The person's edited tasks, as ResearchTasks of the current plan with a raised attempt."""
    plan = state.get("plan")
    if plan is None:
        return []
    by_id = {t.task_id: t for t in plan.tasks}
    by_worker = {t.worker: t for t in plan.tasks}
    tasks: list[ResearchTask] = []
    for edit in edits:
        base = by_id.get(str(edit.get("task_id") or ""))
        if base is None:
            try:
                base = by_worker.get(WorkerType(edit.get("worker")))
            except ValueError:
                base = None
        if base is None or base.worker in {t.worker for t in tasks}:
            continue
        goal = str(edit.get("goal") or base.goal).strip() or base.goal
        focus = str(edit.get("focus") or base.focus).strip() or base.focus
        tasks.append(base.model_copy(update={"goal": goal, "focus": focus, "attempt": base.attempt + 1}))
    return tasks


def human_review_node(state: OrchestrationState) -> OrchestrationState:
    """Pause for the person's decision, then record it for the router."""
    print("\n[orchestrator] NODE: human_review")
    answer = _auto_decision(state)
    if answer is None:
        answer = interrupt(review_payload(state))
    if not isinstance(answer, dict):
        answer = {"decision": str(answer)}
    choice = answer.get("decision") if answer.get("decision") in DECISIONS else "continue"
    update: dict[str, Any] = {"review_decision": choice, "review_note": answer.get("note")}
    if choice == "replan":
        edited = _tasks_from(list(answer.get("tasks") or []), state)
        if edited:
            # The person chose the tasks: dispatch exactly these, without the replan node.
            update["replan_tasks"] = edited
            update["replan_count"] = (state.get("replan_count") or 0) + 1
            update["human_tasks"] = True
        else:
            update["human_tasks"] = False
    return update


def route_after_review(state: OrchestrationState) -> Any:
    """continue -> synthesis (or finalize when nothing was verified); replan; stop -> finalize."""
    from .routing import route_from_plan

    choice = state.get("review_decision") or "continue"
    if choice == "stop":
        return "finalize"
    if choice == "replan":
        if state.get("human_tasks"):
            return route_from_plan(state)
        return "replan"
    decision = state.get("verification")
    result = state.get("verification_result")
    verified = any(
        v.verification_status is FindingCheckStatus.VERIFIED for v in (getattr(result, "findings", None) or [])
    )
    if decision is not None and decision.status is VerificationStatus.CANNOT_COMPLETE and not verified:
        return "finalize"
    return "cross_signal"
