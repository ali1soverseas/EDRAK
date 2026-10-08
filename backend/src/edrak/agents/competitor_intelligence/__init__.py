
from __future__ import annotations
from ...contracts.request import BusinessContext, BusinessRequest, CompanyProfile, TriggerType, UseCase
import pprint
from typing import Any

from ...contracts.task import ResearchTask, WorkerType

from .graph import app
from .nodes import save_node_output , build_worker_result


__all__ = [
    "app",
    "run_research",
    "CompetitorAgent",
]


class CompetitorAgent:
    """Adapter for the orchestrator's shared ResearchTask contract."""

    worker_type = WorkerType.COMPETITOR_INTELLIGENCE

    def run(
        self,
        task: ResearchTask,
        *,
        print_result: bool = False,
    ):
        return run_research(
            task=task,
            print_result=print_result,
        )


def _task_to_initial_state(task: ResearchTask) -> dict[str, Any]:
    """
    Convert the shared ResearchTask into the state expected by
    the competitor-intelligence graph.
    """

    company = task.company_profile.name
    business_context = task.business_context

    return {
        # IMPORTANT: needed later to build WorkerResult
        "task_id": task.task_id,

        "company": company,

        "company_profile": {
            "name": task.company_profile.name,
            "aliases": list(task.company_profile.aliases),
            "products": list(task.company_profile.products),
        },

        "business_context": {
            "use_case": business_context.use_case.value,
            "targets": list(business_context.targets),
            "focus_areas": list(business_context.focus_areas),
            "trigger": business_context.trigger.value,
            "constraints": list(business_context.constraints),
        },

        "research_goal": task.goal,
        "research_focus": task.focus,

        "competitors": list(business_context.targets),

        "research_stage": 1,
        "search_results": [],
        "executed_queries": [],
        "findings": [],
        "verified_findings": [],
        "requirement_checks": [],
        "missing_information": [],
    }

def run_research(
    task: ResearchTask,
    recursion_limit: int = 100,
    print_result: bool = True,
):
    """
    Run competitor-intelligence research from a shared ResearchTask
    and return the standardized WorkerResult contract.
    """

    if task.worker != WorkerType.COMPETITOR_INTELLIGENCE:
        raise ValueError(
            f"Expected COMPETITOR_INTELLIGENCE task, "
            f"got {task.worker}"
        )

    initial_state = _task_to_initial_state(task)

    save_node_output(
        "initial_input",
        initial_state,
        1,
    )

    result = app.invoke(
        initial_state,
        config={
            "recursion_limit": recursion_limit
        },
    )

    # Build the standardized worker contract.
    worker_result = build_worker_result(
        result
    )

    if print_result:
        print("\n" + "=" * 80)
        print("[competitor_intelligence] WORKER RESULT")
        print("=" * 80)

        pprint.pprint(
            worker_result.model_dump(
                mode="json"
            ),
            sort_dicts=False,
        )

    return worker_result