from typing import Literal

from pydantic import BaseModel, Field

WorkerName = Literal["internal", "competitor", "market", "customer_trends"]


class ResearchTask(BaseModel):
    """Shared agent input contract."""

    task_id: str
    run_id: str
    worker: WorkerName
    goal: str
    business_context: str = ""
    focus: str | None = None
    constraints: list[str] = Field(default_factory=list)
