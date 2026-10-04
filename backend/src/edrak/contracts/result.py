"""Finding and WorkerResult models returned by all EDRAK intelligence workers."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

from edrak.contracts.evidence import Evidence


class WorkerStatus(str, Enum):
    """Execution status for a worker result."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NO_DATA = "no_data"


class Finding(BaseModel):
    """An analytical finding/insight deduced from one or more pieces of evidence."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    statement: str = Field(
        ...,
        description="The concise factual insight or strategic observation.",
    )
    domain_topic: str = Field(
        ...,
        description="Category/topic (e.g. 'product_features', 'pricing', 'tech_stack', 'internal_blockers', 'swot_strength').",
    )
    confidence: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Worker's confidence in this specific finding.",
    )
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="IDs of Evidence objects supporting this finding (for provenance & verification).",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional structured metadata (e.g. impact, urgency, metrics).",
    )


class WorkerResult(BaseModel):
    """Standardized output structure returned by every domain worker to Orchestration/Verification."""

    task_id: str = Field(
        ...,
        description="Reference to the executed ResearchTask ID.",
    )
    worker_role: str = Field(
        ...,
        description="Role of the worker (e.g., 'internal_intelligence').",
    )
    status: WorkerStatus = Field(
        default=WorkerStatus.SUCCESS,
        description="Execution status.",
    )
    summary: str = Field(
        ...,
        description="High-level synthesis summary of worker's findings for the task objective.",
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="List of structured findings produced by the worker.",
    )
    evidence: List[Evidence] = Field(
        default_factory=list,
        description="Full list of Evidence items cited by findings.",
    )
    limitations_and_gaps: List[str] = Field(
        default_factory=list,
        description="Known information gaps, data deficiencies, or assumptions.",
    )
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the worker finished execution.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Worker diagnostic or execution metadata (e.g., query count, latency).",
    )
