"""Competitor intelligence worker: researches the competitors and compares them with the company."""

from __future__ import annotations

import logging
import pprint
from datetime import date
from typing import Any

from ...contracts.task import ResearchTask, WorkerType
from ...contracts.vocabulary import competitor_names, markets_of
from ...contracts.result import WorkerResult, WorkerStatus
from ...core.action_log import log_action
from .graph import app
from .nodes import build_worker_result, save_node_output
from .sources import domain_of, register_official_domains

logger = logging.getLogger(__name__)

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


def _competitors_for(task: ResearchTask) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Competitor names and what is known about each. Discovers them when none were given."""
    context = task.business_context
    info: dict[str, dict[str, Any]] = {}
    for ref in context.competitors:
        info[ref.name] = {
            "website": ref.website,
            "links": [{"platform": a.platform.value, "url": a.url} for a in ref.social_accounts],
            "discovered_by": ref.discovered_by,
            "aliases": [],
        }
    names = competitor_names(context)
    for name in names:
        info.setdefault(name, {"website": None, "links": [], "discovered_by": "user", "aliases": []})

    if not names:
        from .discovery import discover_competitors

        log_action("competitor_intelligence", "no competitors were named, so looking for them first")
        found = discover_competitors(
            company=task.company_profile.name,
            description=task.company_profile.notes,
            offerings=list(task.company_profile.products) + ([context.offering] if context.offering else []),
            markets=markets_of(context),
            target_customers=context.target_customers,
            exclude=list(task.company_profile.aliases),
            limit=4,
        )
        for c in found:
            names.append(c["name"])
            info[c["name"]] = {
                "website": c.get("website"),
                "links": c.get("links", []),
                "discovered_by": "agent",
                "reason": c.get("reason"),
                "confidence": c.get("confidence"),
                "aliases": [],
            }

    for name, details in info.items():
        if details.get("website"):
            register_official_domains(name, [domain_of(details["website"])])
    return names, info


def _task_to_initial_state(task: ResearchTask) -> dict[str, Any]:
    """The shared ResearchTask as the competitor graph's state."""
    profile = task.company_profile
    context = task.business_context
    competitors, info = _competitors_for(task)
    return {
        "task_id": task.task_id,
        "attempt": task.attempt,
        "today": date.today().isoformat(),
        "company": profile.name,
        "company_profile": {
            "name": profile.name,
            "aliases": list(profile.aliases),
            "products": list(profile.products),
            "notes": profile.notes,
            "website": profile.website,
        },
        "business_context": {
            "use_case": context.use_case.value,
            "competitors": competitors,
            "markets": markets_of(context),
            "offering": context.offering,
            "target_customers": context.target_customers,
            "focus_areas": list(context.focus_areas),
            "time_window_days": context.time_window_days,
            "constraints": list(context.constraints),
        },
        "research_goal": task.goal,
        "research_focus": task.focus,
        "competitors": competitors,
        "competitor_info": info,
        "research_stage": 1,
        "search_results": [],
        "executed_queries": [],
        "findings": [],
        "verified_findings": [],
        "requirement_checks": [],
        "missing_information": [],
        "dropped_sources": [],
    }


def run_research(
    task: ResearchTask,
    recursion_limit: int = 60,
    print_result: bool = True,
) -> WorkerResult:
    """Run competitor research for one task and return the shared WorkerResult."""
    if task.worker != WorkerType.COMPETITOR_INTELLIGENCE:
        raise ValueError(f"Expected COMPETITOR_INTELLIGENCE task, got {task.worker}")

    initial_state = _task_to_initial_state(task)
    if not initial_state["competitors"]:
        return WorkerResult(
            task_id=task.task_id,
            worker=WorkerType.COMPETITOR_INTELLIGENCE,
            status=WorkerStatus.NO_EVIDENCE,
            attempt=task.attempt,
            gaps=["No competitors were named and none could be found from the company profile."],
        )
    save_node_output("initial_input", initial_state, 1)

    result = app.invoke(initial_state, config={"recursion_limit": recursion_limit})
    worker_result = build_worker_result(result)

    if print_result:
        print("\n" + "=" * 80)
        print("[competitor_intelligence] WORKER RESULT")
        print("=" * 80)
        pprint.pprint(worker_result.model_dump(mode="json"), sort_dicts=False)

    return worker_result
