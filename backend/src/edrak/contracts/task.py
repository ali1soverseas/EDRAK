from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from .base import ContractModel, NonBlankStr, new_id, utcnow
from .request import BusinessContext, CompanyProfile


class WorkerType(str, Enum):
    INTERNAL_INTELLIGENCE = "internal_intelligence"
    COMPETITOR_INTELLIGENCE = "competitor_intelligence"
    MARKET_INTELLIGENCE = "market_intelligence"
    CUSTOMER_TRENDS = "customer_trends"


class ResearchTask(ContractModel):
    """One bounded research assignment handed to a single worker.

    The orchestrator produces this; a worker consumes it. It carries no
    questions list, dependency graph, or required-evidence list on purpose:
    ``goal`` and ``focus`` are the whole instruction surface, and the shared
    company and business context travel with the task so the worker never needs
    the original request.
    """

    task_id: NonBlankStr = Field(default_factory=new_id, description="Unique task identifier.")
    parent_request_id: NonBlankStr = Field(
        description="request_id of the originating BusinessRequest.",
    )
    worker: WorkerType = Field(description="Which domain worker owns this task.")

    goal: NonBlankStr = Field(description="What this task must achieve.")
    focus: NonBlankStr = Field(description="What this task must concentrate on.")

    company_profile: CompanyProfile = Field(description="Company baseline, copied from the request.")
    business_context: BusinessContext = Field(description="Business context, copied from the request.")

    attempt: int = Field(
        default=1,
        ge=1,
        description="Starts at 1 and increments on targeted retries.",
    )


class ResearchPlan(ContractModel):
    """An ordered set of research assignments produced by the planner."""

    plan_id: NonBlankStr = Field(default_factory=new_id, description="Unique plan identifier.")
    request_id: NonBlankStr = Field(
        description="request_id of the originating BusinessRequest.",
    )
    tasks: list[ResearchTask] = Field(description="Tasks to dispatch.")
    created_at: datetime = Field(
        default_factory=utcnow,
        description="When the plan was created (UTC).",
    )
    rationale: str | None = Field(
        default=None,
        description="Why these tasks, in plain language.",
    )

    @model_validator(mode="after")
    def _tasks_belong_to_this_request(self) -> ResearchPlan:
        """A plan must not carry another request's tasks.

        Nothing downstream would notice a mismatch: verification groups results
        by request_id, so the task would silently vanish from this request's
        report while another request carried a task it never planned.
        """
        strays = [
            task.task_id
            for task in self.tasks
            if task.parent_request_id != self.request_id
        ]
        if strays:
            raise ValueError(
                f"task parent_request_id does not match plan request_id "
                f"{self.request_id!r}: {sorted(strays)}"
            )
        return self

    @model_validator(mode="after")
    def _task_ids_are_unique(self) -> ResearchPlan:
        """Duplicate task_id would be silently merged by the dispatch reducer."""
        task_ids = [task.task_id for task in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            duplicates = sorted({t for t in task_ids if task_ids.count(t) > 1})
            raise ValueError(f"duplicate task_id in plan tasks: {duplicates}")
        return self