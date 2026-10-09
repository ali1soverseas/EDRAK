from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..contracts import (
    BusinessRequest,
    NonBlankStr,
    ResearchPlan,
    ResearchTask,
    WorkerType,
)
from ..core.llm import get_chat_model
from .prompts import PLANNER_SYSTEM_PROMPT

MAX_REPAIR_ATTEMPTS = 1


class PlanningError(RuntimeError):
    """The planner LLM produced output that could not be turned into a ResearchPlan.

    Carries the raw text so a failed run can be diagnosed without re-running
    the model. Deliberately fatal: there is no deterministic fallback plan,
    because a silently substituted plan would hide a broken model or a
    missing API key behind a plausible-looking result.
    """

    def __init__(self, reason: str, raw_output: str = "") -> None:
        self.reason = reason
        self.raw_output = raw_output
        super().__init__(reason)


class WorkerAssignment(BaseModel):
    """The narrow slice of ResearchTask the LLM is allowed to author.

    company_profile, business_context, parent_request_id, task_id, and attempt
    are stamped deterministically from the request, so the model never
    authors them and four tasks cannot drift apart.
    """

    worker: WorkerType
    goal: NonBlankStr
    focus: NonBlankStr


class Planner(Protocol):
    def plan(self, request: BusinessRequest) -> ResearchPlan: ...


class PlannerPlan(BaseModel):
    """The reply shape: one entry per worker assignment."""

    assignments: list[WorkerAssignment]


class LlmPlanner:
    def __init__(self, *, max_repair_attempts: int = MAX_REPAIR_ATTEMPTS) -> None:
        self.max_repair_attempts = max_repair_attempts

    def plan(self, request: BusinessRequest) -> ResearchPlan:
        context = self._build_context(request)

        try:
            assignments = self._invoke(context)
        except PlanningError as exc:
            if self.max_repair_attempts < 1:
                raise
            assignments = self._invoke(
                f"{context}\n\nYour previous reply was rejected: {exc.reason}\n"
                "Return a corrected plan covering the relevant workers."
            )

        return self._build_plan(request, assignments)

    def _invoke(self, prompt: str) -> list[WorkerAssignment]:
        """Ask for a plan, validated against the assignment schema.

        json_mode rather than function_calling. This is the one call site that
        needs a list of objects, and gpt-oss:120b does not reliably emit a tool
        call for a list-of-objects schema here: it answers with prose or a bare
        JSON array instead, and the binding then yields nothing. json_mode does
        return parseable JSON, and because the schema constrains `worker` to the
        real enum, an invented name is rejected rather than silently accepted.
        Every other call site uses function_calling.
        """
        plan = get_chat_model(temperature=0).with_structured_output(
            PlannerPlan, method="json_mode"
        ).invoke(PLANNER_SYSTEM_PROMPT + "\n\n" + prompt)

        if plan is None or not plan.assignments:
            raise PlanningError("model returned no usable plan", "")
        return list(plan.assignments)

    def _build_plan(
        self,
        request: BusinessRequest,
        assignments: list[WorkerAssignment],
    ) -> ResearchPlan:
        tasks = [
            ResearchTask(
                parent_request_id=request.request_id,
                worker=assignment.worker,
                goal=assignment.goal,
                focus=assignment.focus,
                company_profile=request.company_profile,
                business_context=request.business_context,
            )
            for assignment in assignments
        ]
        return ResearchPlan(
            request_id=request.request_id,
            tasks=tasks,
            rationale=f"LLM-generated plan from {len(tasks)} assignment(s).",
        )

    def _build_context(self, request: BusinessRequest) -> str:
        return (
            f"Business objective: {request.goal}\n"
            f"Company: {request.company_profile.name}\n"
            f"Use case: {request.business_context.use_case.value}\n"
            f"Targets: {', '.join(request.business_context.targets) or '(none)'}\n"
            f"Focus areas: {', '.join(request.business_context.focus_areas) or '(none)'}\n"
            f"Constraints: {'; '.join(request.business_context.constraints) or '(none)'}"
        )
