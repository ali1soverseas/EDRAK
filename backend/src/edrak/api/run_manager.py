"""Run execution and lifecycle management for EDRAK analyses."""

from __future__ import annotations

import asyncio
import json
import logging
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

            # Invoke orchestration graph
            logger.info("Starting orchestration graph execution for analysis %s", analysis_id)
            state = await graph.ainvoke({"request": request})

            results = state.get("results") or []
            res_by_worker = {r.worker: r for r in results}

            # Update tasks progress
            now = now_iso()
            for tv in run_view.tasks:
                wr = res_by_worker.get(tv.worker)
                if wr:
                    is_success = wr.status in (WorkerStatus.COMPLETED, WorkerStatus.PARTIAL)
                    tv.state = "done" if is_success else "failed"
                    tv.progress = 100 if is_success else 0
                    tv.sources = len(wr.evidence)
                    tv.activity = "Research complete" if is_success else (wr.error or "Task failed")
                    tv.log.append(
                        {
                            "at": now,
                            "text": f"Found {len(wr.findings)} findings across {len(wr.evidence)} sources.",
                        }
                    )
                    if not is_success:
                        tv.error = wr.error
                else:
                    tv.state = "failed"
                    tv.error = "No worker result produced"
                await stream.emit("task_update", tv.model_dump(mode="json"))

            # Stage 3: Verification
            run_view.stage = "verify"
            run_view.verification = "running"
            await stream.emit("stage_change", {"stage": "verify"})
            await stream.emit("verification", {"status": "running"})

            verification: Optional[VerificationResult] = state.get("verification_result")
            run_view.verification = "done"
            await stream.emit("verification", {"status": "done"})

            # Stage 4: Synthesis
            run_view.stage = "synthesize"
            await stream.emit("stage_change", {"stage": "synthesize"})

            # Stage 5: Finalize and generate Brief
            run_view.stage = "brief"
            await stream.emit("stage_change", {"stage": "brief"})

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


# Global singleton instance
run_manager = RunManager()
