from typing import Literal

from pydantic import BaseModel, Field

WorkerStatus = Literal["success", "partial", "failed"]


class Evidence(BaseModel):
    type: str
    source: str
    excerpt: str | None = None


class Finding(BaseModel):
    claim: str
    evidence: list[Evidence] = Field(default_factory=list)
    task: str | None = None


class WorkerResult(BaseModel):
    """Shared worker output contract. Raw documents stay off this object."""

    worker: str
    task_id: str
    run_id: str
    status: WorkerStatus
    findings: list[Finding] = Field(default_factory=list)
    notes: str | None = None
