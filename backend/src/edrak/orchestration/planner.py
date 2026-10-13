from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..contracts import (
    BusinessRequest,
    NonBlankStr,
    ResearchPlan,
    ResearchTask,
    WorkerType,
)
from ..contracts.vocabulary import describe_context
from ..core.llm import get_chat_model
from ..metrics.planning import requirements_for
from ..metrics.custom import register, to_defs
from ..metrics.profile import DYNAMIC, business_model
from .prompts import PLANNER_SYSTEM_PROMPT

MAX_REPAIR_ATTEMPTS = 1

PROPOSE_METRICS = """This company's field has no standard set of business figures. Also return "metrics": up to 5 figures
that would most change the decision, each a number that is published somewhere (company pages, filings,
statistics offices, industry reports). Never a score you would have to compute, never a vague index.
Each item: {"name": "short name", "definition": "what exactly is counted, one sentence",
"unit_kind": "currency" | "percent" | "count" | "quantity" | "date",
"owner": "competitor_intelligence" (a figure per competitor) | "market_intelligence" (a figure per market) |
"customer_trends" | "internal_intelligence" (our own figure, from our documents),
"search_phrase": "2 to 6 words to search for it, without the company or place",
"reads_as": "one sentence on what the figure tells the decision maker"}.
Do not repeat market size, market growth, regulation, price or capacity: those are already asked for."""


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
    # What is being assessed, as a short search phrase ("sovereign AI infrastructure"), when the form named no offering.
    topic: str | None = None
    # Only for a field the fixed metric sets do not cover: the figures that would decide the question.
    # Loosely typed on purpose: a malformed proposal is dropped by metrics.custom.to_defs, never fails the plan.
    metrics: list[dict[str, Any]] = Field(default_factory=list)


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
        """Ask for a plan, validated against the assignment schema.

        json_mode rather than function_calling. This is the one call site that
        needs a list of objects, and gpt-oss:120b does not reliably emit a tool
        call for a list-of-objects schema here: it answers with prose or a bare
        JSON array instead, and the binding then yields nothing. json_mode does
        return parseable JSON, and because the schema constrains `worker` to the
        real enum, an invented name is rejected rather than silently accepted.
        Every other call site uses function_calling.
        """
        plan = get_chat_model(agent="orchestrator", temperature=0).with_structured_output(
            PlannerPlan, method="json_mode"
        ).invoke(PLANNER_SYSTEM_PROMPT + "\n\n" + prompt)

        if plan is None or not plan.assignments:
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
        topic = " ".join((reply.topic or "").split()[:6]).strip(" .")
        if not (ctx.offering or "").strip() and topic:
            # Every worker and the metrics agent search for this, so "Latin America market size" becomes
            # "sovereign AI infrastructure Latin America market size".
            ctx = ctx.model_copy(update={"offering": topic})
        model = business_model(request.company_profile, offering=ctx.offering, goal=request.goal)
        custom: list[str] = []
        if model == DYNAMIC:
            defs = to_defs(reply.metrics)
            if defs:
                # Saved on the request so the run, the brief and a restart all use the same figures. The caller
                # persists `extras` with the request.
                request.extras["custom_metrics"] = [d.model_dump(mode="json") for d in defs]
            custom = register(request.extras.get("custom_metrics") or [])
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
                    competitors, list(ctx.markets), ctx.offering or "", model, custom,
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
        ctx = request.business_context
        if business_model(request.company_profile, offering=ctx.offering, goal=request.goal) == DYNAMIC:
            lines.append(PROPOSE_METRICS)
        return "\n".join(lines)
