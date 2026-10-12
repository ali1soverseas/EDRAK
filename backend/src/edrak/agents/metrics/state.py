"""State of the metrics agent: the entity-by-metric grid and what has been found for it."""

from __future__ import annotations

from typing import Any, TypedDict

from edrak.contracts import BusinessRequest, ResearchTask, WorkerResult
from edrak.metrics.contracts import MetricObservation, MetricRequirement


class Cell(TypedDict):
    """One square of the grid: a metric for an entity, owned by one planned task."""

    task_id: str
    requirement: MetricRequirement


class MetricsState(TypedDict, total=False):
    request: BusinessRequest
    tasks: list[ResearchTask]
    # The workers' results. The agent adds observations to them, and the evidence its hunt fetched.
    results: list[WorkerResult]
    grid: list[Cell]
    # Observations by the task_id of the result whose evidence backs them.
    observations: dict[str, list[MetricObservation]]
    # Hunts run per cell key; at most MAX_HUNTS each.
    hunts: dict[str, int]
    # Cells whose hunt read at least one of the entity's own pages (for "not publicly disclosed").
    read_official: list[str]
    conflicts: list[str]
    log: list[dict[str, Any]]
