"""Ingest one EDRAK run into the graph, stage by stage.

    from edrak.graph import record_run

    report = record_run(
        request,                      # BusinessRequest
        orchestration=result,         # OrchestrationResult: plan, results, cross_signal, decision_analysis
        verification=verification,    # VerificationResult (not carried by OrchestrationResult)
        workspace_id=workspace_id,
    )

Order matters: findings must exist before verdicts can JUDGE them, verdicts before
cross-signals (which only cite verified findings), signals before the decision
items that cite them, and verdicts before the domain projection.

A failed stage is logged and recorded in the report; the remaining stages still
run. Nothing here raises into the pipeline unless ``strict=True`` (tests, loader).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from ..connection import GraphConnection, GraphUnavailable, get_connection
from ..entities import EntityIndex, enum_value, get, result_key
from ..profile_snapshot import ProfileSnapshot, ingest_profile_snapshot
from .writers import (
    ingest_catalog,
    ingest_cross_signal,
    ingest_decision,
    ingest_plan,
    ingest_request,
    ingest_verification,
    ingest_worker_result,
    project_domain,
    read_catalog,
    sync_metric_registry,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class IngestReport:
    request_id: str
    stages: dict[str, str] = field(default_factory=dict)  # stage -> "ok" | "skipped: ..." | "failed: ..."
    counts: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(v.startswith("failed") for v in self.stages.values())

    def summary(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.stages.items())


class GraphIngestor:
    _registry_synced = False  # Metric definitions are synced once per process

    def __init__(self, conn: GraphConnection | None = None, *, strict: bool = False) -> None:
        self.conn = conn or get_connection()
        self.strict = strict

    def ingest_run(
        self,
        request: Any,
        *,
        workspace_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        plan: Any = None,
        results: list[Any] | None = None,
        orchestration: Any = None,
        verification: Any = None,
        cross_signal: Any = None,
        decision: Any = None,
        catalog: list[Any] | None = None,
        profile_snapshot: ProfileSnapshot | None = None,
    ) -> IngestReport:
        """``catalog``: curated features, pricing plans, regulations, themes, platforms and extra
        products (see writers.ingest_catalog). Entities the workspace already tracks are read back
        and linked too, so the catalog only needs passing when it changes."""
        request_id = get(request, "request_id")
        report = IngestReport(request_id=request_id)

        if not self.conn.enabled:
            report.stages["all"] = "skipped: GRAPH_ENABLED is false"
            return report
        if not self.conn.available():
            if self.strict:
                raise GraphUnavailable("Neo4j is unreachable")
            report.stages["all"] = "skipped: Neo4j unreachable"
            return report

        if orchestration is not None:
            plan = plan if plan is not None else get(orchestration, "plan")
            results = results if results is not None else list(get(orchestration, "results") or [])
            cross_signal = cross_signal if cross_signal is not None else get(orchestration, "cross_signal")
            decision = decision if decision is not None else get(orchestration, "decision_analysis")
            status = status or enum_value(get(orchestration, "status"))
        workspace_id = workspace_id or get(get(request, "company_profile"), "workspace_id")

        index = self._stage(
            report, "request",
            lambda: ingest_request(self.conn, request, workspace_id=workspace_id, user_id=user_id, status=status),
        ) or EntityIndex.from_request(request)

        if profile_snapshot is not None:
            if not workspace_id:
                raise ValueError("A workspace ID is required for a profile snapshot")
            self._stage(
                report, "profile_snapshot",
                lambda: ingest_profile_snapshot(
                    self.conn, profile_snapshot, workspace_id, request_id, get(get(request, "company_profile"), "name")
                ),
            )

        if catalog:
            report.counts["catalog"] = self._stage(
                report, "catalog",
                lambda: ingest_catalog(self.conn, catalog, workspace_id=workspace_id, index=index),
            )
        tracked = self._stage(report, "tracked_catalog", lambda: read_catalog(self.conn, workspace_id)) or []
        index.extend_catalog(tracked)

        if not GraphIngestor._registry_synced:
            if self._stage(report, "metric_registry", lambda: sync_metric_registry(self.conn)) is not None:
                GraphIngestor._registry_synced = True

        requirement_keys = None
        if plan is not None:
            requirement_keys = self._stage(
                report, "plan", lambda: ingest_plan(self.conn, plan, request_id=request_id, index=index)
            )

        for result in results or []:
            name = f"result:{enum_value(get(result, 'worker'))}:{result_key(get(result, 'task_id'), get(result, 'attempt'))}"
            report.counts[name] = self._stage(
                report, name,
                lambda r=result: ingest_worker_result(
                    self.conn, r, request_id=request_id, workspace_id=workspace_id,
                    index=index, requirement_keys=requirement_keys,
                ),
            )

        if verification is not None:
            report.counts["verdicts"] = self._stage(
                report, "verification", lambda: ingest_verification(self.conn, verification, request_id=request_id)
            )
        if cross_signal:
            report.counts["signals"] = self._stage(
                report, "cross_signal",
                lambda: ingest_cross_signal(self.conn, cross_signal, request_id=request_id, workspace_id=workspace_id),
            )
        if decision:
            report.counts["decision_items"] = self._stage(
                report, "decision",
                lambda: ingest_decision(self.conn, decision, request_id=request_id, workspace_id=workspace_id),
            )
        if verification is not None:
            report.counts["projection"] = self._stage(
                report, "projection", lambda: project_domain(self.conn, request_id=request_id)
            )

        logger.info("Graph ingestion for %s: %s", request_id, report.summary())
        return report

    async def aingest_run(self, request: Any, **kwargs: Any) -> IngestReport:
        """For the async API service: runs the blocking driver calls in a worker thread."""
        return await asyncio.to_thread(self.ingest_run, request, **kwargs)

    def _stage(self, report: IngestReport, name: str, fn: Callable[[], T]) -> T | None:
        try:
            out = fn()
            report.stages[name] = "ok"
            return out
        except Exception as exc:  # noqa: BLE001 - the graph must never break a run
            logger.exception("Graph ingestion stage %s failed for %s", name, report.request_id)
            report.stages[name] = f"failed: {exc}"
            if self.strict:
                raise
            return None


def record_run(request: Any, **kwargs: Any) -> IngestReport:
    """One-call hook for the end of a pipeline run. Never raises."""
    try:
        return GraphIngestor().ingest_run(request, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Graph ingestion failed")
        report = IngestReport(request_id=str(get(request, "request_id")))
        report.stages["all"] = f"failed: {exc}"
        return report
