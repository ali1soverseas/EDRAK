from typing import Literal

from pydantic import BaseModel, Field

from edrak.contracts.evidence import Finding

WorkerStatus = Literal["success", "partial", "failed"]


class WorkerResult(BaseModel):
    """Shared agent output contract. Raw documents stay off this object."""

    worker: str
    task_id: str
    run_id: str
    status: WorkerStatus
    findings: list[Finding] = Field(default_factory=list)
    notes: str | None = None
