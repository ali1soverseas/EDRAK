"""Run execution and lifecycle management for EDRAK analyses."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from edrak.api.brief_builder import build_brief
from edrak.api.schemas import (
    RunEvent,
    RunEventCallout,
    RunTaskView,
    RunViewOut,
)
from edrak.api.streaming import EventStream
from edrak.core import action_log
from edrak.contracts import (
    BusinessRequest,
    OrchestrationResult,
    ResearchPlan,
    RunStatus,
    UseCase,
    VerificationResult,
    WorkerStatus,
    WorkerType,
)
from edrak.db.connection import get_db
from edrak.db.repository import (
    get_analysis,
    get_next_brief_no,
    get_run as db_get_run,
    save_brief,
    update_analysis_status,
    update_run as db_update_run,
)
from edrak.orchestration.graph import build_graph
from edrak.orchestration.registry import get_default_registry

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveRun:
    def __init__(self, run_view: RunViewOut, stream: EventStream) -> None:
        self.run_view = run_view
        self.stream = stream
        self.task: Optional[asyncio.Task[None]] = None
        # How many log lines a card had when its current round of work began, so progress
        # for a second pass is counted from that pass and not from the first.
        self.round_base: dict[str, int] = {}


class RunManager:
    """Singleton run manager for orchestrating runs and streaming events."""

    def __init__(self) -> None:
        self._runs: dict[str, ActiveRun] = {}

    def get_stream(self, analysis_id: str) -> Optional[EventStream]:
        active = self._runs.get(analysis_id)
        return active.stream if active else None

    async def get_run_view(self, analysis_id: str) -> Optional[RunViewOut]:
        if analysis_id in self._runs:
            return self._runs[analysis_id].run_view

        # Fallback to DB
        async with get_db() as db:
            row = await db_get_run(db, analysis_id)
            if not row:
                return None
            analysis = await get_analysis(db, analysis_id)
            if not analysis:
                return None

            form = json.loads(analysis["form_json"])
            plan = json.loads(analysis["plan_json"]) if analysis.get("plan_json") else None

            tasks: list[RunTaskView] = []
            if plan and "tasks" in plan:
                for t in plan["tasks"]:
                    wtype = WorkerType(t.get("worker", "competitor_intelligence"))
                    tasks.append(
                        RunTaskView(
                            task_id=t.get("task_id", ""),
                            worker=wtype,
                            state="done" if row["state"] in ("completed", "partial") else "failed",
                            progress=100 if row["state"] in ("completed", "partial") else 0,
                            sources=0,
                            activity="Completed",
                            log=[],
                            error=None,
                        )
                    )

            brief_id = f"brief-{analysis_id}" if row["state"] in ("completed", "partial") else None

            return RunViewOut(
                analysis_id=analysis_id,
                title=analysis["title"],
                use_case=UseCase(form.get("use_case", "competitive_intelligence")),
                state=row["state"],
                stage=row["stage"],
                approved_at=row["approved_at"],
                approved_by=row["approved_by"],
                started_at=row["started_at"] or row["approved_at"],
                finished_at=row["finished_at"],
                tasks=tasks,
                events=[],
                verification="done" if row["state"] in ("completed", "partial") else "waiting",
                brief_id=brief_id,
            )

    # ---- live activity ----------------------------------------------------------------

    _AGENT_TO_WORKER = {
        "competitor_intelligence": WorkerType.COMPETITOR_INTELLIGENCE,
        "market_intelligence": WorkerType.MARKET_INTELLIGENCE,
        "internal_intelligence": WorkerType.INTERNAL_INTELLIGENCE,
        "customer_trends": WorkerType.CUSTOMER_TRENDS,
    }
    _MAX_LOG_LINES = 300

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
            active.stream.emit_nowait("task_update", tv.model_dump(mode="json"))

    def _restart_card(self, active: ActiveRun, tv: RunTaskView, text: str, now: str) -> None:
        """Put a finished card back to work for another pass, with progress counted afresh."""
        tv.state = "running"
        tv.progress = 30
        tv.error = None
        tv.activity = text
        tv.log.append({"at": now, "text": text})
        active.round_base[tv.task_id] = len(tv.log)

    async def _event(
        self,
        active: ActiveRun,
        *,
        kind: str,
        message: str,
        source: str = "supervisor",
        callout: Optional[RunEventCallout] = None,
    ) -> None:
        """Add a line to the Supervisor log, where the polled snapshot and the stream both see it."""
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

    async def start_run(self, analysis_id: str, approved_by: str) -> None:
        async with get_db() as db:
            analysis = await get_analysis(db, analysis_id)
            if not analysis:
                raise ValueError("Analysis not found")

            form = json.loads(analysis["form_json"])
            request_data = json.loads(analysis["request_json"])
            plan_data = json.loads(analysis["plan_json"]) if analysis.get("plan_json") else None

            if not plan_data:
                raise ValueError("Plan not drafted yet")

            plan = ResearchPlan.model_validate(plan_data)
            request = BusinessRequest.model_validate(request_data)

            now = now_iso()
            # Initialize Run tasks
            task_views: list[RunTaskView] = [
                RunTaskView(
                    task_id=t.task_id,
                    worker=t.worker,
                    state="queued",
                    progress=0,
                    sources=0,
                    activity=f"Queued for {t.worker.value}",
                    log=[{"at": now, "text": "Task queued"}],
                    error=None,
                )
                for t in plan.tasks
            ]

            run_view = RunViewOut(
                analysis_id=analysis_id,
                title=analysis["title"],
                use_case=request.business_context.use_case,
                state="running",
                stage="plan",
                approved_at=now,
                approved_by=approved_by,
                started_at=now,
                finished_at=None,
                tasks=task_views,
                events=[
                    RunEvent(
                        event_id=f"ev-{analysis_id}-start",
                        at=now,
                        source="supervisor",
                        kind="info",
                        message=f"Plan approved by {approved_by}. Launching {len(plan.tasks)} worker tasks.",
                        callout=None,
                    )
                ],
                verification="waiting",
                brief_id=None,
            )

            stream = EventStream()
            active_run = ActiveRun(run_view=run_view, stream=stream)
            self._runs[analysis_id] = active_run

            # Persist run start in DB
            from edrak.db.repository import create_or_replace_run

            await create_or_replace_run(
                db,
                analysis_id=analysis_id,
                approved_at=now,
                approved_by=approved_by,
                started_at=now,
                state="running",
                stage="plan",
            )
            await update_analysis_status(db, analysis_id, "running")

            # Spawn async background orchestration task
            active_run.task = asyncio.create_task(
                self._execute_run(analysis_id, request, plan, analysis["title"], approved_by)
            )

    async def cancel_run(self, analysis_id: str) -> None:
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
            await db_update_run(
                db,
                analysis_id=analysis_id,
                state="cancelled",
                finished_at=now,
                cancelled_at=now,
            )
            await update_analysis_status(db, analysis_id, "cancelled")

    async def _execute_run(
        self,
        analysis_id: str,
        request: BusinessRequest,
        plan: ResearchPlan,
        analysis_title: str,
        requested_by: str,
    ) -> None:
        active = self._runs.get(analysis_id)
        if not active:
            return

        run_view = active.run_view
        stream = active.stream
        sub_token = None

        try:
            # Stage 1: Dispatch
            run_view.stage = "dispatch"
            await stream.emit("stage_change", {"stage": "dispatch"})
            now = now_iso()

            for tv in run_view.tasks:
                tv.state = "running"
                tv.progress = 25
                tv.activity = f"Connecting to data sources for {tv.worker.value}"
                tv.log.append({"at": now, "text": "Worker dispatched"})
                await stream.emit("task_update", tv.model_dump(mode="json"))

            # Stage 2: Workers
            run_view.stage = "workers"
            await stream.emit("stage_change", {"stage": "workers"})

            # Build registry & graph
            registry = get_default_registry()
            graph = build_graph(registry)

            # Invoke orchestration graph with live event streaming
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

                    # Update local state
                    for k, v in node_output.items():
                        if k in ("results", "outcomes"):
                            from edrak.orchestration.state import merge_by_task_id
                            state[k] = merge_by_task_id(state.get(k) or [], v)
                        else:
                            state[k] = v

                    now = now_iso()

                    if node_name == "dispatch":
                        for wr in node_output.get("results") or []:
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
                                        active,
                                        source=wr.worker.value,
                                        kind="gap" if gap else "info",
                                        message=f"Complete. {summary}" + (f" Gap reported: {gap}" if gap else ""),
                                    )
                                elif wr.status == WorkerStatus.NO_EVIDENCE:
                                    # It ran and searched, and found nothing. That is not a crash.
                                    reason = gap or "No matching evidence was found."
                                    tv.state, tv.progress, tv.error = "done", 100, None
                                    tv.activity = f"No evidence found. {reason}"
                                    tv.log.append({"at": now, "text": f"No evidence found. {reason}"})
                                    await self._event(
                                        active,
                                        source=wr.worker.value,
                                        kind="gap",
                                        message=f"No evidence found. {reason}",
                                    )
                                else:
                                    reason = wr.error or gap or "The worker stopped without a result."
                                    tv.state, tv.progress, tv.error = "failed", max(tv.progress, 0), reason
                                    tv.activity = f"Failed: {reason}"
                                    tv.log.append({"at": now, "text": f"Failed: {reason}"})
                                    await self._event(
                                        active,
                                        source=wr.worker.value,
                                        kind="failure",
                                        message=f"Failed: {reason}",
                                        callout=RunEventCallout(
                                            text=f"Continue without the {name} worker. Verification and the brief will mark the evidence as partial.",
                                            highlight=None,
                                        ),
                                    )
                                await stream.emit("task_update", tv.model_dump(mode="json"))

                        # Once every worker has reported, verification is what is running.
                        if run_view.stage == "workers" and all(
                            tv.state in ("done", "failed") for tv in run_view.tasks
                        ):
                            run_view.stage = "verify"
                            run_view.verification = "running"
                            run_view.verification_progress = 0
                            run_view.verification_activity = "Starting verification"
                            await stream.emit("stage_change", {"stage": "verify"})
                            await stream.emit("verification", {"status": "running"})
                            await self._event(
                                active,
                                kind="info",
                                message="All workers have reported. Verification is checking completeness, source quality and contradictions.",
                            )

                    elif node_name == "verify":
                        v_res = node_output.get("verification_result")
                        v_dec = node_output.get("verification")
                        status_val = v_dec.status.value if v_dec else "assessed"
                        findings_count = len(v_res.findings) if v_res else 0
                        run_view.verification = "done"
                        run_view.verification_progress = 100
                        await stream.emit("verification", {"status": "done"})
                        await self._event(
                            active,
                            kind="info",
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
                                active,
                                kind="replan",
                                message=v_dec.summary if v_dec else "Verification asked for more research.",
                                callout=RunEventCallout(
                                    text=f"Re-running {len(reasons_by_worker)} worker(s) on {len(actions)} point(s): {names}.",
                                    highlight=None,
                                ),
                            )
                        else:
                            run_view.stage = "synthesize"
                            await stream.emit("stage_change", {"stage": "synthesize"})
                            await self._event(
                                active,
                                kind="info",
                                message="Looking for patterns across the workers' findings.",
                            )

                    elif node_name == "replan":
                        replan_tasks = node_output.get("replan_tasks") or []
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
                        await self._event(
                            active, kind="info", message="Cross-domain signals identified. Preparing the decision analysis."
                        )

                    elif node_name == "decision_analysis":
                        await self._event(
                            active, kind="info", message="Decision analysis written. Building the brief."
                        )

                    elif node_name == "finalize":
                        run_view.stage = "brief"
                        await stream.emit("stage_change", {"stage": "brief"})

            results = state.get("results") or []
            verification: Optional[VerificationResult] = state.get("verification_result")

            orchestration_result: OrchestrationResult = state.get(
                "orchestration_result"
            ) or OrchestrationResult(
                request_id=request.request_id,
                status=RunStatus.COMPLETED,
                plan=plan,
                results=results,
            )

            is_partial = orchestration_result.status == RunStatus.PARTIAL
            final_run_state = "partial" if is_partial else "completed"
            now = now_iso()

            async with get_db() as db:
                brief_no = await get_next_brief_no(db)
                brief_id = f"brief-{analysis_id}"

                brief_dict = build_brief(
                    brief_id=brief_id,
                    brief_no=brief_no,
                    analysis_id=analysis_id,
                    analysis_title=analysis_title,
                    title=f"Strategic Analysis: {request.goal}",
                    use_case=request.business_context.use_case.value,
                    created_at=now,
                    requested_by=requested_by,
                    request=request,
                    orchestration=orchestration_result,
                    verification=verification,
                    cross_signal=state.get("cross_signal"),
                    decision_analysis=state.get("decision_analysis"),
                )

                await save_brief(db, brief_id, analysis_id, brief_no, brief_dict)
                await db_update_run(
                    db,
                    analysis_id=analysis_id,
                    state=final_run_state,
                    stage="brief",
                    finished_at=now,
                    result_dict=orchestration_result.model_dump(mode="json"),
                )
                await update_analysis_status(
                    db,
                    analysis_id=analysis_id,
                    status=final_run_state,
                    brief_no=brief_no,
                )

            run_view.state = final_run_state
            run_view.finished_at = now
            run_view.brief_id = brief_id

            await stream.emit("complete", {"state": final_run_state, "brief_id": brief_id})
            stream.close()
            logger.info("Analysis %s run completed successfully", analysis_id)

        except asyncio.CancelledError:
            logger.info("Analysis %s run cancelled", analysis_id)
            run_view.state = "cancelled"
            run_view.finished_at = now_iso()
            await stream.emit("complete", {"state": "cancelled", "brief_id": None})
            stream.close()

        except Exception as exc:
            logger.exception("Analysis %s run failed with error: %s", analysis_id, exc)
            now = now_iso()
            run_view.state = "failed"
            run_view.finished_at = now
            await stream.emit("error", {"message": str(exc)})
            await stream.emit("complete", {"state": "failed", "brief_id": None})
            stream.close()

            async with get_db() as db:
                await db_update_run(
                    db,
                    analysis_id=analysis_id,
                    state="failed",
                    finished_at=now,
                )
                await update_analysis_status(db, analysis_id, "failed")

        finally:
            action_log.unsubscribe(analysis_id, sub_token)


# Global singleton instance
run_manager = RunManager()
