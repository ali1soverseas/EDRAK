from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel

from ..contracts import (
    BusinessRequest,
    NonBlankStr,
    ResearchPlan,
    ResearchTask,
    WorkerType,
)
from ..contracts.vocabulary import describe_context
from ..core.llm import DEFAULT_MAX_TOKENS, get_chat_model, message_text
from ..metrics.planning import requirements_for
from .prompts import get_planner_prompt

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
    rationale: str | None = None


class LlmPlanner:
    def __init__(self, *, max_repair_attempts: int = MAX_REPAIR_ATTEMPTS) -> None:
        self.max_repair_attempts = max_repair_attempts

    def plan(self, request: BusinessRequest) -> ResearchPlan:
        context = self._build_context(request)

        try:
            reply = self._invoke(context)
        except PlanningError as exc:
            if self.max_repair_attempts < 1:
                raise
            reply = self._invoke(
                f"{context}\n\nYour previous reply was rejected: {exc.reason}\n"
                "Return a corrected plan covering the relevant workers."
            )

        return self._build_plan(request, reply)

    def _invoke(self, prompt: str) -> PlannerPlan:
        """Ask the shared NVIDIA model for a plan and validate its JSON response."""
        system_prompt = get_planner_prompt()
        reply = get_chat_model(agent="orchestrator", temperature=0.2).invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        raw_output = message_text(reply)

        clean_output = re.sub(
            r"^\s*```(?:json)?\s*|\s*```\s*$",
            "",
            raw_output,
            flags=re.IGNORECASE,
        ).strip()
        try:
            parsed = json.loads(clean_output)
            plan = PlannerPlan.model_validate(parsed)
        except (json.JSONDecodeError, ValueError) as exc:
            raise PlanningError(
                f"NVIDIA planner response did not match the plan schema: {exc}",
                raw_output,
            ) from exc

        if not plan.assignments:
            raise PlanningError("model returned no usable plan", "")
        return plan

    def _build_plan(
        self,
        request: BusinessRequest,
        reply: PlannerPlan,
    ) -> ResearchPlan:
        # One task per worker: the dispatcher runs workers in parallel and a second task for
        # the same worker would collide with the first.
        assignments: list[WorkerAssignment] = []
        for assignment in reply.assignments:
            if assignment.worker not in {a.worker for a in assignments}:
                assignments.append(assignment)
        ctx = request.business_context
        competitors = [c.name for c in ctx.competitors] or list(ctx.targets)
        tasks = [
            ResearchTask(
                parent_request_id=request.request_id,
                worker=assignment.worker,
                goal=assignment.goal,
                focus=assignment.focus,
                company_profile=request.company_profile,
                business_context=ctx,
                requirements=requirements_for(
                    assignment.worker.value, ctx.use_case.value, request.company_profile.name,
                    competitors, list(ctx.markets), ctx.offering or "",
                ),
            )
            for assignment in assignments
        ]
        return ResearchPlan(
            request_id=request.request_id,
            tasks=tasks,
            rationale=(reply.rationale or "").strip()
            or f"{len(tasks)} workers investigate in parallel; verification then checks what they found.",
        )

    def _build_context(self, request: BusinessRequest) -> str:
        lines = [
            f"Business objective: {request.goal}",
            describe_context(request.company_profile, request.business_context),
        ]
        if request.business_context.constraints:
            lines.append(f"Constraints: {'; '.join(request.business_context.constraints)}")
        allowed = request.extras.get("allowed_source_types")
        if allowed:
            lines.append(f"Sources the user allowed: {', '.join(allowed)}")
        feedback = request.extras.get("rejected_plan_feedback")
        if feedback:
            lines.append(f"The user rejected the previous plan with this feedback: {feedback}")
        return "\n".join(lines)
