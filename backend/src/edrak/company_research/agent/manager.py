"""Runs the research graph as a background task, one active run per company.

The pattern is the analysis run manager's: an asyncio task per run kept in memory, progress written
to the run's row as it goes, and a cancel that stops the task and marks the row. Unlike analyses,
a research run is not resumable; one that was running when the server stopped is failed at startup
(`fail_orphaned_research_runs`).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any, Optional

from edrak.company_research.agent.graph import RECURSION_LIMIT, build_research_graph
from edrak.company_research.agent.nodes import NoWebsite, build_stats, resolve_seed, tidy_warnings
from edrak.company_research.agent.state import ResearchDeps, ResearchState
from edrak.company_research.models import ResearchRun, ResearchRunStatus
from edrak.db.connection import get_db
from edrak.db.repositories import company as company_repo
from edrak.db.repositories import research_runs as runs_repo

logger = logging.getLogger(__name__)

__all__ = ["NoWebsite", "ResearchRunManager", "research_manager"]


class ResearchRunManager:
    """Owns the research runs executing in this process."""

    def __init__(self, deps: Optional[ResearchDeps] = None) -> None:
        self.deps = deps  # replaced in tests; the real network and model when None
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def is_active(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return bool(task and not task.done())

    async def start(self, workspace_id: str, *, seed_url: Optional[str] = None) -> ResearchRun:
        """Queue a run and start it. Raises NoWebsite, UrlBlocked, or ResearchRunActive (one run at a time)."""
        async with get_db() as db:
            company = await company_repo.get_company(db, workspace_id)
            url = resolve_seed(company, seed_url)  # fails here, not in a run that could never work
            run = await runs_repo.create_research_run(db, workspace_id=workspace_id, input={"seed_url": url})
        task = asyncio.create_task(self._execute(run.id, workspace_id, url))
        self._tasks[run.id] = task
        task.add_done_callback(lambda _t, run_id=run.id: self._tasks.pop(run_id, None))
        return run

    async def cancel(self, workspace_id: str, run_id: str) -> bool:
        """Stop a run. False when it is not queued or running."""
        async with get_db() as db:
            run = await runs_repo.get_research_run(db, workspace_id, run_id)
            if run is None or run.status not in (ResearchRunStatus.QUEUED, ResearchRunStatus.RUNNING):
                return False
            await runs_repo.finish_research_run(db, run_id, status=ResearchRunStatus.CANCELLED)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=5)
        return True

    async def wait(self, run_id: str) -> None:
        """Wait for a run's task to end. For tests and shutdown."""
        task = self._tasks.get(run_id)
        if task:
            await asyncio.wait({task})

    # ------------------------------------------------------------------ the run itself

    def _deps(self) -> ResearchDeps:
        return dataclasses.replace(self.deps or ResearchDeps(), progress=self._progress)

    @staticmethod
    async def _progress(state: ResearchState, stage: str) -> None:
        async with get_db() as db:
            await runs_repo.update_research_run(
                db, state["run_id"], stage=stage, pages_crawled=state.get("pages_crawled", 0),
                pdfs_read=state.get("pdfs_read", 0), facts_found=state.get("created") or len(state.get("pending", [])),
                warnings=tidy_warnings(state.get("warnings", [])), stats=build_stats(state),
            )

    async def _execute(self, run_id: str, workspace_id: str, seed_url: str) -> None:
        try:
            async with get_db() as db:
                await runs_repo.update_research_run(db, run_id, status=ResearchRunStatus.RUNNING, stage="seed")
            deps = self._deps()
            graph = build_research_graph(deps)
            initial: dict[str, Any] = {"workspace_id": workspace_id, "run_id": run_id, "seed_url": seed_url, "warnings": []}
            await asyncio.wait_for(graph.ainvoke(initial, {"recursion_limit": RECURSION_LIMIT}), deps.options.max_run_s)
            await self._close_if_open(run_id, "The run ended before it could record its result.")
        except asyncio.TimeoutError:
            await self._close_if_open(run_id, "The run took longer than its time limit and was stopped. Nothing was saved.")
        except asyncio.CancelledError:
            await asyncio.shield(self._close_if_open(run_id, None, ResearchRunStatus.CANCELLED))
            raise
        except Exception:
            logger.exception("Research run %s failed", run_id)
            await self._close_if_open(run_id, "The research run stopped with an unexpected error.")

    @staticmethod
    async def _close_if_open(run_id: str, error: Optional[str], status: ResearchRunStatus = ResearchRunStatus.FAILED) -> None:
        """End the run if nothing has: a no-op when the graph's own `finish` or a cancel already did."""
        async with get_db() as db:
            await runs_repo.finish_research_run(db, run_id, status=status, error=error)


research_manager = ResearchRunManager()
