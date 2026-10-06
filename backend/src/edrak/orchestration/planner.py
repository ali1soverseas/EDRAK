from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from ..contracts import (
    BusinessRequest,
    NonBlankStr,
    ResearchPlan,
    ResearchTask,
    WorkerType,
)
from ..core.llm import build_chat_model
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


class LlmPlanner:
    def __init__(self, *, max_repair_attempts: int = MAX_REPAIR_ATTEMPTS) -> None:
        self.max_repair_attempts = max_repair_attempts

    def plan(self, request: BusinessRequest) -> ResearchPlan:
        llm = build_chat_model()
        context = self._build_context(request)

        raw = self._invoke(llm, PLANNER_SYSTEM_PROMPT, context)
        assignments, failure = self._parse(raw)

        for _ in range(self.max_repair_attempts):
            if assignments is not None:
                break
            raw = self._invoke(
                llm,
                PLANNER_SYSTEM_PROMPT,
                f"{context}\n\nYour previous reply was rejected: {failure}\n"
                "Return only the corrected JSON array of worker assignments.",
            )
            assignments, failure = self._parse(raw)

        if assignments is None:
            raise PlanningError(failure, raw)

        return self._build_plan(request, assignments)

    def _invoke(self, llm: Any, system: str, user: str) -> str:
        response = llm.invoke(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        return self._extract_text(response)

    def _extract_text(self, response: Any) -> str:
        content = getattr(response, "content", "")

        if isinstance(content, list):
            parts = [
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            ]
            text = "".join(parts).strip()
        else:
            text = str(content).strip()

        if text:
            return text

        reasoning = self._extract_reasoning(response)
        if reasoning:
            raise PlanningError(
                "model returned no content; it appears to have produced only "
                f"reasoning output ({len(reasoning)} chars)",
                reasoning,
            )

        raise PlanningError("model returned empty content", "")

    @staticmethod
    def _extract_reasoning(response: Any) -> str:
        additional = getattr(response, "additional_kwargs", None) or {}
        for key in ("reasoning", "reasoning_content"):
            value = additional.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _parse(self, raw: str) -> tuple[list[WorkerAssignment] | None, str]:
        payload = self._loads(raw)
        if payload is None:
            return None, "reply was not valid JSON"

        items = payload if isinstance(payload, list) else None
        if items is None and isinstance(payload, dict):
            items = payload.get("assignments")
        if not isinstance(items, list):
            return None, "expected a JSON array of assignments"

        assignments: list[WorkerAssignment] = []
        seen: set[WorkerType] = set()
        errors: list[str] = []

        for index, item in enumerate(items):
            try:
                assignment = WorkerAssignment.model_validate(item)
            except ValidationError as exc:
                errors.append(f"[{index}] {exc.error_count()} field error(s)")
                continue

            if assignment.worker in seen:
                continue
            seen.add(assignment.worker)
            assignments.append(assignment)

        if not items:
            return None, "assignment list was empty"
        if errors:
            return None, f"{len(errors)} assignment(s) failed validation: {'; '.join(errors)}"
        if not assignments:
            return None, "no valid assignments after de-duplication"
        return assignments, ""

    @staticmethod
    def _loads(raw: str) -> Any:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except ValueError:
            return None

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