"""Run execution and lifecycle for EDRAK analyses.

A run executes in memory (an asyncio task driving the orchestration graph) and streams to the run
screen over SSE. Everything the screen shows is also written to the database as it happens:
task cards (research_tasks), the Supervisor log and worker activity (run_events), worker results
(worker_results, evidence, findings, ...), verification passes and the brief. A finished run, or
one opened after a restart, is rebuilt from those tables.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Optional

from edrak.api.brief_builder import build_brief
from edrak.api.schemas import RunEvent, RunEventCallout, RunTaskView, RunViewOut
from edrak.api.streaming import EventStream
from edrak.contracts import (
    BusinessRequest,
    OrchestrationResult,
    ResearchPlan,
    RunStatus,
    UseCase,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.core import action_log
from edrak.db.connection import get_db
from edrak.db.repositories import analyses as analyses_repo
from edrak.db.repositories import briefs as briefs_repo
from edrak.db.repositories import results as results_repo
from edrak.db.repositories import runs as runs_repo
from edrak.db.util import fetch_one, now_iso
from edrak.orchestration.graph import build_graph
from edrak.orchestration.registry import get_default_registry

logger = logging.getLogger(__name__)

# The run screen calls task states queued/running/done/failed; the table calls them queued/working/finished/failed.
VIEW_TO_DB_STATE = {"queued": "queued", "running": "working", "done": "finished", "failed": "failed"}
DB_TO_VIEW_STATE = {v: k for k, v in VIEW_TO_DB_STATE.items()}


class ActiveRun:
    def __init__(self, run_view: RunViewOut, stream: EventStream) -> None:
        self.run_view = run_view
        self.stream = stream
        self.task: Optional[asyncio.Task[None]] = None
        # How many log lines a card had when its current round of work began, so progress
        # for a second pass is counted from that pass and not from the first.
        self.round_base: dict[str, int] = {}
        # Activity lines and card changes waiting to be written to the database.
        self.pending_lines: list[tuple[str, Optional[str], str, str]] = []  # (channel, task_id, actor, text)
        self.dirty_cards: set[str] = set()
        self.verification_dirty = False
        self.requested_by_id: Optional[str] = None
        self.workspace_id: Optional[str] = None


class RunManager:
    """Owns the runs executing in this process."""

    _AGENT_TO_WORKER = {
        "competitor_intelligence": WorkerType.COMPETITOR_INTELLIGENCE,
        "market_intelligence": WorkerType.MARKET_INTELLIGENCE,
        "internal_intelligence": WorkerType.INTERNAL_INTELLIGENCE,
        "customer_trends": WorkerType.CUSTOMER_TRENDS,
    }
    _MAX_LOG_LINES = 300
    _FLUSH_SECONDS = 1.0

    def __init__(self) -> None:
        self._runs: dict[str, ActiveRun] = {}

    def get_stream(self, analysis_id: str) -> Optional[EventStream]:
        active = self._runs.get(analysis_id)
        return active.stream if active else None

    def is_active(self, analysis_id: str) -> bool:
        active = self._runs.get(analysis_id)
        return bool(active and active.task and not active.task.done())

    # ------------------------------------------------------------------ reading a run

    async def get_run_view(self, analysis_id: str, workspace_id: Optional[str] = None) -> Optional[RunViewOut]:
        active = self._runs.get(analysis_id)
        if active and (workspace_id is None or active.workspace_id in (None, workspace_id)):
            return active.run_view
        async with get_db() as db:
            return await self.view_from_db(db, analysis_id, workspace_id)

    async def view_from_db(self, db: Any, analysis_id: str, workspace_id: Optional[str]) -> Optional[RunViewOut]:
        request = await analyses_repo.get_request(db, analysis_id, workspace_id)
        run = await runs_repo.get_run(db, analysis_id)
        if not request or not run:
            return None
        plan = await fetch_one(
            db,
            "SELECT p.*, u.full_name AS approved_by_name FROM research_plans p LEFT JOIN users u ON u.id = p.approved_by WHERE p.plan_id = ?",
            (run["plan_id"],),
        ) or {}
        tasks = await analyses_repo.plan_tasks(db, run["plan_id"], enabled_only=True)
        events = await runs_repo.list_events(db, analysis_id)
        brief = await briefs_repo.get_brief_for_request(db, analysis_id)

        logs: dict[str, list[dict[str, str]]] = {}
        verification_log: list[dict[str, str]] = []
        supervisor: list[RunEvent] = []
        for e in events:
            if e["channel"] == "worker_feed" and e["task_id"]:
                logs.setdefault(e["task_id"], []).append({"at": e["occurred_at"], "text": e["message"]})
            elif e["channel"] == "verification_feed":
                verification_log.append({"at": e["occurred_at"], "text": e["message"]})
            else:
                supervisor.append(
                    RunEvent(
                        event_id=f"ev-{e['id']}",
                        at=e["occurred_at"],
                        source=e["actor"],
                        kind=runs_repo.DB_TO_VIEW_KIND.get(e["kind"], "info"),
                        message=e["message"],
                        callout=RunEventCallout(**e["callout"]) if e["callout"] else None,
                    )
                )
        approver = "auto-approved" if plan.get("auto_approved") else (plan.get("approved_by_name") or "")
        return RunViewOut(
            analysis_id=analysis_id,
            title=request["title"],
            use_case=UseCase(request["use_case"]),
            state=run["status"] if run["status"] != "queued" else "running",
            stage=run["current_stage"] if run["current_stage"] != "done" else "brief",
            approved_at=plan.get("approved_at") or run["created_at"],
            approved_by=approver,
            started_at=run["started_at"] or run["created_at"],
            finished_at=run["completed_at"],
            tasks=[
                RunTaskView(
                    task_id=t["task_id"],
                    worker=WorkerType(t["worker"]),
                    state=DB_TO_VIEW_STATE.get(t["state"], "queued"),
                    progress=t["progress"],
                    sources=t["sources"],
                    activity=t["status_line"] or "",
                    log=logs.get(t["task_id"], [])[-self._MAX_LOG_LINES:],
                    error=t["error"],
                )
                for t in tasks
            ],
            events=supervisor,
            verification=run["verification_state"],
            verification_activity=run["verification_activity"] or "",
            verification_progress=run["verification_progress"],
            verification_log=verification_log[-self._MAX_LOG_LINES:],
            brief_id=brief["id"] if brief else None,
        )

    # ------------------------------------------------------------------ live activity

    def _apply_activity(self, active: ActiveRun, agent: str, message: str) -> None:
        """A worker did something: show it on its card now, not when it finishes.

        Runs on the event loop. Workers call from their own threads, so they reach this
        through `call_soon_threadsafe`.
        """
        text = next((line.strip() for line in message.splitlines() if line.strip()), "")
        if not text:
            return
        text = text if len(text) <= 240 else text[:239].rstrip() + "…"
        if agent == "verification":
            view = active.run_view
            if view.verification != "running" or text.startswith("[verification]"):
                return  # only live steps; the stage's captured printout is for the log file
            view.verification_activity = text
            view.verification_log.append({"at": now_iso(), "text": text})
            if len(view.verification_log) > self._MAX_LOG_LINES:
                del view.verification_log[: len(view.verification_log) - self._MAX_LOG_LINES]
            done = re.search(r"finding (\d+) of (\d+)", text)
            if done and int(done.group(2)) > 0:
                view.verification_progress = max(view.verification_progress, int(100 * (int(done.group(1)) - 1) / int(done.group(2))))
            active.pending_lines.append(("verification_feed", None, "verification", text))
            active.verification_dirty = True
            return
        worker = self._AGENT_TO_WORKER.get(agent)
        if worker is None:
            return
        for tv in active.run_view.tasks:
            if tv.worker != worker or tv.state not in ("queued", "running"):
                continue
            tv.state = "running"
            tv.activity = text
            tv.log.append({"at": now_iso(), "text": text})
            if len(tv.log) > self._MAX_LOG_LINES:
                del tv.log[: len(tv.log) - self._MAX_LOG_LINES]
            # Workers do not report a percentage, so progress rises with the number of steps
            # taken and levels off below 100. It only reaches 100 when the worker finishes.
            steps = len(tv.log) - active.round_base.get(tv.task_id, 0)
            estimate = 25 + int(67 * (1 - 0.93 ** max(steps, 0)))
            tv.progress = max(tv.progress, min(92, estimate))
            active.pending_lines.append(("worker_feed", tv.task_id, worker.value, text))
            active.dirty_cards.add(tv.task_id)
            active.stream.emit_nowait("task_update", tv.model_dump(mode="json"))

    def _restart_card(self, active: ActiveRun, tv: RunTaskView, text: str, now: str) -> None:
        """Put a finished card back to work for another pass, with progress counted afresh."""
        tv.state = "running"
        tv.progress = 30
        tv.error = None
        tv.activity = text
        tv.log.append({"at": now, "text": text})
        active.round_base[tv.task_id] = len(tv.log)
        active.pending_lines.append(("worker_feed", tv.task_id, tv.worker.value, text))
        active.dirty_cards.add(tv.task_id)

    async def _flush(self, active: ActiveRun) -> None:
        """Write buffered activity lines and changed cards."""
        lines, active.pending_lines = active.pending_lines, []
        cards, active.dirty_cards = active.dirty_cards, set()
        verification_dirty, active.verification_dirty = active.verification_dirty, False
        if not (lines or cards or verification_dirty):
            return
        request_id = active.run_view.analysis_id
        try:
            async with get_db() as db:
                for channel, task_id, actor, text in lines:
                    await runs_repo.add_event(
                        db, request_id, channel=channel, actor=actor, kind="progress", message=text,
                        task_id=task_id, commit=False,
                    )
                for tv in active.run_view.tasks:
                    if tv.task_id in cards:
                        await analyses_repo.update_task_fields(
                            db, tv.task_id, state=VIEW_TO_DB_STATE.get(tv.state, "queued"), progress=tv.progress,
                            sources=tv.sources, status_line=tv.activity[:500] or None, error=tv.error,
                        )
                view = active.run_view
                await runs_repo.update_run(
                    db, request_id, commit=False, current_stage=view.stage,
                    verification_state=view.verification, verification_progress=view.verification_progress,
                    verification_activity=view.verification_activity or None,
                )
                await db.commit()
        except Exception:  # noqa: BLE001 - the live screen must keep going even if a write fails
            logger.exception("Could not save live activity for %s", request_id)

    async def _flusher(self, active: ActiveRun) -> None:
        while True:
            await asyncio.sleep(self._FLUSH_SECONDS)
            await self._flush(active)

    async def _event(
        self,
        active: ActiveRun,
        *,
        kind: str,
        message: str,
        source: str = "supervisor",
        callout: Optional[RunEventCallout] = None,
    ) -> None:
        """Add a line to the Supervisor log: the screen, the stream and the database all get it."""
        event = RunEvent(
            event_id=f"ev-{active.run_view.analysis_id}-{uuid.uuid4().hex[:8]}",
            at=now_iso(),
            source=source,
            kind=kind,
            message=message,
            callout=callout,
        )
        active.run_view.events.append(event)
        await active.stream.emit("event", event.model_dump(mode="json"))
        try:
            async with get_db() as db:
                await runs_repo.add_event(
                    db, active.run_view.analysis_id, channel="supervisor_log", actor=source, kind=kind,
                    message=message, callout=callout.model_dump() if callout else None, occurred_at=event.at,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Could not save a supervisor event")

    # ------------------------------------------------------------------ starting and stopping

    async def start_run(self, analysis_id: str, *, user_id: Optional[str], approver_name: str, auto: bool = False) -> None:
        """Start the run of an analysis whose plan was just approved (checkpoint 1)."""
        async with get_db() as db:
            row = await analyses_repo.get_request(db, analysis_id)
            if not row:
                raise ValueError("not_found")
            plan_row = await analyses_repo.current_plan(db, analysis_id)
            if not plan_row or plan_row["status"] != "approved":
                raise ValueError("plan_not_approved")
            request = analyses_repo.request_contract(row)
            plan = await analyses_repo.plan_contract(db, request, plan_row, enabled_only=True)
            if not plan.tasks:
                raise ValueError("plan_has_no_tasks")

            await runs_repo.create_run(db, request_id=analysis_id, plan_id=plan.plan_id, triggered_by=user_id, commit=False)
            await analyses_repo.set_request_status(db, analysis_id, "running", commit=False)
            for t in plan.tasks:
                await analyses_repo.update_task_fields(
                    db, t.task_id, state="queued", progress=0, status_line=f"Queued for {t.worker.value.replace('_', ' ')}"
                )
            await db.commit()

        now = now_iso()
        run_view = RunViewOut(
            analysis_id=analysis_id,
            title=row["title"],
            use_case=request.business_context.use_case,
            state="running",
            stage="plan",
            approved_at=now,
            approved_by=approver_name,
            started_at=now,
            finished_at=None,
            tasks=[
                RunTaskView(
                    task_id=t.task_id, worker=t.worker, state="queued", progress=0, sources=0,
                    activity=f"Queued for {t.worker.value.replace('_', ' ')}", log=[{"at": now, "text": "Task queued"}],
                )
                for t in plan.tasks
            ],
            events=[],
            verification="waiting",
            brief_id=None,
        )
        active = ActiveRun(run_view=run_view, stream=EventStream())
        active.requested_by_id = row.get("created_by") or user_id
        active.workspace_id = row["workspace_id"]
        self._runs[analysis_id] = active
        who = "automatically (monitor setting)" if auto else f"by {approver_name}"
        await self._event(
            active, kind="decision",
            message=f"Plan approved {who}. Launching {len(plan.tasks)} worker task{'s' if len(plan.tasks) != 1 else ''}.",
        )
        active.task = asyncio.create_task(self._execute_run(analysis_id, request, plan, row["title"]))

    async def cancel_run(self, analysis_id: str) -> bool:
        active = self._runs.get(analysis_id)
        now = now_iso()
        if active:
            if active.task and not active.task.done():
                active.task.cancel()
            active.run_view.state = "cancelled"
            active.run_view.finished_at = now
            await active.stream.emit("complete", {"state": "cancelled", "brief_id": None})
            active.stream.close()
        async with get_db() as db:
            run = await runs_repo.get_run(db, analysis_id)
            if not run or run["status"] not in runs_repo.ACTIVE:
                return False
            await runs_repo.update_run(db, analysis_id, status="cancelled", completed_at=now, commit=False)
            await analyses_repo.set_request_status(db, analysis_id, "cancelled", commit=False)
            await db.execute(
                "UPDATE research_tasks SET state = 'failed', error = 'Cancelled' WHERE parent_request_id = ? AND state IN ('queued','working')",
                (analysis_id,),
            )
            await db.commit()
        return True

    # ------------------------------------------------------------------ the run itself

    async def _store_results(self, results: list[WorkerResult], request_id: str) -> None:
        async with get_db() as db:
            for wr in results:
                try:
                    await results_repo.write_worker_result(db, request_id, wr, commit=False)
                except Exception:  # noqa: BLE001 - keep the run going; the brief still has the result in memory
                    logger.exception("Could not store the result of task %s", wr.task_id)
                await analyses_repo.update_task_fields(db, wr.task_id, attempt=wr.attempt)
            await db.commit()

    async def _store_verification(self, verification: VerificationResult, results: list[WorkerResult], request_id: str) -> Optional[str]:
        gaps: list[dict[str, Any]] = []
        for wr in results:
            for g in wr.gaps:
                gaps.append({"worker": wr.worker.value, "subject": g, "suggested_action": "investigate"})
        for m in verification.control_summary.missing_information:
            gaps.append({"subject": m, "suggested_action": "investigate"})
        try:
            async with get_db() as db:
                vid = await results_repo.write_verification(db, verification, commit=False)
                await results_repo.write_gaps(db, request_id, gaps, commit=False)
                await db.commit()
                return vid
        except Exception:  # noqa: BLE001
            logger.exception("Could not store verification for %s", request_id)
            return None

    async def _store_replan_tasks(self, plan_id: str, request_id: str, tasks: list[Any], reasons: dict[str, str]) -> None:
        async with get_db() as db:
            known = await analyses_repo.task_ids(db, plan_id)
            position = len(known)
            for task in tasks:
                if task.task_id in known:
                    await analyses_repo.update_task_fields(db, task.task_id, attempt=task.attempt, state="working", progress=30)
                else:
                    await analyses_repo.insert_task(
                        db, plan_id, task, position=position, origin="replan",
                        reason=reasons.get(task.worker.value) or "Added by the Supervisor after verification.",
                    )
                    position += 1
            await db.commit()

    async def _execute_run(
        self,
        analysis_id: str,
        request: BusinessRequest,
        plan: ResearchPlan,
        analysis_title: str,
    ) -> None:
        active = self._runs.get(analysis_id)
        if not active:
            return

        run_view = active.run_view
        stream = active.stream
        sub_token = None
        flusher = asyncio.create_task(self._flusher(active))

        try:
            run_view.stage = "dispatch"
            await stream.emit("stage_change", {"stage": "dispatch"})
            now = now_iso()
            for tv in run_view.tasks:
                tv.state = "running"
                tv.progress = 25
                tv.activity = f"Connecting to data sources for {tv.worker.value.replace('_', ' ')}"
                tv.log.append({"at": now, "text": "Worker dispatched"})
                active.pending_lines.append(("worker_feed", tv.task_id, tv.worker.value, "Worker dispatched"))
                active.dirty_cards.add(tv.task_id)
                await stream.emit("task_update", tv.model_dump(mode="json"))

            run_view.stage = "workers"
            await stream.emit("stage_change", {"stage": "workers"})

            graph = build_graph(get_default_registry())
            logger.info("Starting orchestration graph execution for analysis %s", analysis_id)
            # Run the plan that was approved. Without it the graph plans again by itself.
            state: dict[str, Any] = {"request": request, "plan": plan}

            # From here until the run ends, every line a worker logs also reaches its card.
            loop = asyncio.get_running_loop()
            sub_token = action_log.subscribe(
                analysis_id,
                lambda agent, message: loop.call_soon_threadsafe(self._apply_activity, active, agent, message),
            )
            async for chunk in graph.astream({"request": request, "plan": plan}, stream_mode="updates"):
                for node_name, node_output in chunk.items():
                    logger.info("LangGraph node [%s] completed", node_name)
                    if not isinstance(node_output, dict):
                        continue
                    for k, v in node_output.items():
                        if k in ("results", "outcomes"):
                            from edrak.orchestration.state import merge_by_task_id
                            state[k] = merge_by_task_id(state.get(k) or [], v)
                        else:
                            state[k] = v
                    await self._on_node(active, node_name, node_output, state, plan, analysis_id)

            await self._finish(active, request, plan, analysis_title, state)

        except asyncio.CancelledError:
            logger.info("Analysis %s run cancelled", analysis_id)
            run_view.state = "cancelled"
            run_view.finished_at = now_iso()
            await stream.emit("complete", {"state": "cancelled", "brief_id": None})
            stream.close()

        except Exception as exc:  # noqa: BLE001
            logger.exception("Analysis %s run failed with error: %s", analysis_id, exc)
            now = now_iso()
            run_view.state = "failed"
            run_view.finished_at = now
            await self._event(active, kind="failure", message=f"The run stopped: {exc}"[:500])
            await stream.emit("error", {"message": str(exc)})
            await stream.emit("complete", {"state": "failed", "brief_id": None})
            stream.close()
            async with get_db() as db:
                await runs_repo.update_run(db, analysis_id, status="failed", completed_at=now, error=str(exc)[:1000], commit=False)
                await analyses_repo.set_request_status(db, analysis_id, "failed", failure_reason=str(exc)[:1000], commit=False)
                await db.commit()

        finally:
            flusher.cancel()
            await self._flush(active)
            action_log.unsubscribe(analysis_id, sub_token)

    async def _on_node(
        self,
        active: ActiveRun,
        node_name: str,
        node_output: dict[str, Any],
        state: dict[str, Any],
        plan: ResearchPlan,
        analysis_id: str,
    ) -> None:
        run_view = active.run_view
        stream = active.stream
        now = now_iso()

        if node_name == "dispatch":
            results: list[WorkerResult] = list(node_output.get("results") or [])
            await self._store_results(results, analysis_id)
            for wr in results:
                for tv in run_view.tasks:
                    if tv.worker != wr.worker:
                        continue
                    name = wr.worker.value.replace("_", " ")
                    gap = wr.gaps[0] if wr.gaps else None
                    tv.sources = len(wr.evidence)
                    if wr.status in (WorkerStatus.COMPLETED, WorkerStatus.PARTIAL):
                        summary = f"Found {len(wr.findings)} findings across {len(wr.evidence)} sources."
                        tv.state, tv.progress, tv.error = "done", 100, None
                        tv.activity = summary
                        tv.log.append({"at": now, "text": summary + (f" Gap: {gap}" if gap else "")})
                        await self._event(
                            active, source=wr.worker.value, kind="gap" if gap else "info",
                            message=f"Complete. {summary}" + (f" Gap reported: {gap}" if gap else ""),
                        )
                    elif wr.status == WorkerStatus.NO_EVIDENCE:
                        # It ran and searched, and found nothing. That is not a crash.
                        reason = gap or "No matching evidence was found."
                        tv.state, tv.progress, tv.error = "done", 100, None
                        tv.activity = f"No evidence found. {reason}"
                        tv.log.append({"at": now, "text": f"No evidence found. {reason}"})
                        await self._event(active, source=wr.worker.value, kind="gap", message=f"No evidence found. {reason}")
                    else:
                        reason = wr.error or gap or "The worker stopped without a result."
                        tv.state, tv.progress, tv.error = "failed", max(tv.progress, 0), reason
                        tv.activity = f"Failed: {reason}"
                        tv.log.append({"at": now, "text": f"Failed: {reason}"})
                        await self._event(
                            active, source=wr.worker.value, kind="failure", message=f"Failed: {reason}",
                            callout=RunEventCallout(
                                text=f"Continue without the {name} worker. Verification and the brief will mark the evidence as partial.",
                                highlight=None,
                            ),
                        )
                    active.dirty_cards.add(tv.task_id)
                    await stream.emit("task_update", tv.model_dump(mode="json"))

            # Once every worker has reported, verification is what is running.
            if run_view.stage == "workers" and all(tv.state in ("done", "failed") for tv in run_view.tasks):
                run_view.stage = "verify"
                run_view.verification = "running"
                run_view.verification_progress = 0
                run_view.verification_activity = "Starting verification"
                active.verification_dirty = True
                await stream.emit("stage_change", {"stage": "verify"})
                await stream.emit("verification", {"status": "running"})
                await self._event(
                    active, kind="info",
                    message="All workers have reported. Verification is checking completeness, source quality and contradictions.",
                )

        elif node_name == "verify":
            v_res = node_output.get("verification_result")
            v_dec = node_output.get("verification")
            status_val = v_dec.status.value if v_dec else "assessed"
            findings_count = len(v_res.findings) if v_res else 0
            if v_res is not None:
                await self._store_verification(v_res, list(state.get("results") or []), analysis_id)
            run_view.verification = "done"
            run_view.verification_progress = 100
            active.verification_dirty = True
            await stream.emit("verification", {"status": "done"})
            await self._event(
                active, source="verification", kind="info",
                message=f"Verification completed: {findings_count} findings evaluated ({status_val.replace('_', ' ')}).",
            )
            if status_val in ("replan_required", "retry_required"):
                # Verification wants some workers to go again. Put them back to
                # work on screen now, so their new activity shows up as it happens.
                actions = list(v_dec.targeted_actions) if v_dec else []
                run_view.stage = "workers"
                run_view.verification = "waiting"
                await stream.emit("stage_change", {"stage": "workers"})
                reasons_by_worker: dict[WorkerType, list[str]] = {}
                for action in actions:
                    reasons_by_worker.setdefault(action.worker, []).append(action.reason)
                for tv in run_view.tasks:
                    reasons = reasons_by_worker.get(tv.worker)
                    if reasons:
                        more = f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else ""
                        self._restart_card(active, tv, f"Going again: {reasons[0]}{more}", now)
                        await stream.emit("task_update", tv.model_dump(mode="json"))
                names = ", ".join(w.value.replace("_", " ") for w in reasons_by_worker)
                await self._event(
                    active, kind="replan",
                    message=v_dec.summary if v_dec else "Verification asked for more research.",
                    callout=RunEventCallout(
                        text=f"Re-running {len(reasons_by_worker)} worker(s) on {len(actions)} point(s): {names}.",
                        highlight=None,
                    ),
                )
            else:
                run_view.stage = "synthesize"
                await stream.emit("stage_change", {"stage": "synthesize"})
                await self._event(active, kind="info", message="Looking for patterns across the workers' findings.")

        elif node_name == "replan":
            replan_tasks = node_output.get("replan_tasks") or []
            v_dec = state.get("verification")
            reasons = {a.worker.value: a.reason for a in (v_dec.targeted_actions if v_dec else [])}
            await self._store_replan_tasks(plan.plan_id, analysis_id, list(replan_tasks), reasons)
            run_view.stage = "workers"
            run_view.verification = "waiting"
            await stream.emit("stage_change", {"stage": "workers"})
            for tv in run_view.tasks:
                if any(rt.worker == tv.worker for rt in replan_tasks):
                    self._restart_card(active, tv, "Re-dispatched for targeted refinement", now)
                    await stream.emit("task_update", tv.model_dump(mode="json"))

        elif node_name == "cross_signal":
            run_view.stage = "synthesize"
            await stream.emit("stage_change", {"stage": "synthesize"})
            await self._event(active, source="synthesis", kind="info",
                              message="Cross-domain signals identified. Preparing the decision analysis.")

        elif node_name == "decision_analysis":
            await self._event(active, source="synthesis", kind="info", message="Decision analysis written. Building the brief.")

        elif node_name == "finalize":
            run_view.stage = "brief"
            await stream.emit("stage_change", {"stage": "brief"})

    async def _finish(
        self,
        active: ActiveRun,
        request: BusinessRequest,
        plan: ResearchPlan,
        analysis_title: str,
        state: dict[str, Any],
    ) -> None:
        run_view = active.run_view
        analysis_id = run_view.analysis_id
        results = state.get("results") or []
        verification: Optional[VerificationResult] = state.get("verification_result")
        orchestration_result: OrchestrationResult = state.get("orchestration_result") or OrchestrationResult(
            request_id=request.request_id, status=RunStatus.COMPLETED, plan=plan, results=results,
        )
        is_partial = orchestration_result.status == RunStatus.PARTIAL
        final_state = "partial" if is_partial else "completed"
        now = now_iso()

        async with get_db() as db:
            requested_by = None
            if active.requested_by_id:
                user = await fetch_one(db, "SELECT full_name FROM users WHERE id = ?", (active.requested_by_id,))
                requested_by = user["full_name"] if user else None
            numbers = await results_repo.evidence_numbers(db, analysis_id)

            def build(brief_id: str, number: int) -> dict[str, Any]:
                return build_brief(
                    brief_id=brief_id,
                    brief_no=number,
                    analysis_id=analysis_id,
                    analysis_title=analysis_title,
                    title=analysis_title,
                    use_case=request.business_context.use_case.value,
                    created_at=now,
                    requested_by=requested_by or "",
                    request=request,
                    orchestration=orchestration_result,
                    verification=verification,
                    cross_signal=state.get("cross_signal"),
                    decision_analysis=state.get("decision_analysis"),
                    evidence_numbers=numbers,
                )

            saved = await briefs_repo.save_brief(
                db, workspace_id=active.workspace_id or "", request_id=analysis_id,
                requested_by=active.requested_by_id, build=build,
            )
            await runs_repo.update_run(db, analysis_id, status=final_state, current_stage="done", completed_at=now, commit=False)
            await analyses_repo.set_request_status(db, analysis_id, final_state, commit=False)
            await db.commit()

        run_view.state = final_state
        run_view.stage = "brief"
        run_view.finished_at = now
        run_view.brief_id = saved["id"]
        await self._event(active, kind="info", message="The brief is ready.")
        await active.stream.emit("complete", {"state": final_state, "brief_id": saved["id"]})
        active.stream.close()
        logger.info("Analysis %s run completed (%s)", analysis_id, final_state)


# Global singleton instance
run_manager = RunManager()
