"""Builders for shared-contract objects used by the adapter and worker tests."""

from typing import Any

from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)


def research_task(**overrides: Any) -> ResearchTask:
    """A competitive intelligence task for GitLab, the pilot scenario."""
    context = {
        "use_case": UseCase.COMPETITIVE_INTELLIGENCE,
        "targets": ["GitHub Copilot", "Atlassian", "Microsoft Azure DevOps"],
        "focus_areas": ["customer pain points", "sentiment"],
        "time_window_days": 90,
    }
    context.update(overrides.pop("context", {}))
    fields: dict[str, Any] = {
        "task_id": "task-ct-0001",
        "parent_request_id": "request-0001",
        "worker": WorkerType.CUSTOMER_TRENDS,
        "goal": "How do customers perceive GitLab Duo against GitHub Copilot?",
        "focus": "Customer sentiment and pain points around AI-assisted development",
        "company_profile": CompanyProfile(name="GitLab", aliases=["GitLab Inc."], products=["Duo"]),
        "business_context": BusinessContext(**context),
    }
    fields.update(overrides)
    return ResearchTask(**fields)
