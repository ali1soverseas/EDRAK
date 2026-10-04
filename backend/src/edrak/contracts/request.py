from typing import Literal

from pydantic import BaseModel, Field

WorkerName = Literal["internal", "competitor", "market", "customer_trends"]


class BusinessRequest(BaseModel):
    """Inbound research request accepted by the orchestrator."""

    request_id: str
    goal: str
    business_context: str = ""
    company: str | None = None
    use_case: str | None = None


class ResearchTask(BaseModel):
    """Shared worker input contract."""

    task_id: str
    run_id: str
    worker: WorkerName
    goal: str
    business_context: str = ""
    focus: str | None = None
    constraints: list[str] = Field(default_factory=list)
