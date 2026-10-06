from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

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
        description="Stadrts at 1 and increments on targeted retries.",
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