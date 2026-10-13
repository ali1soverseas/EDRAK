from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"

for path in (SRC, ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

import pytest

from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)


@pytest.fixture
def sample_research_task() -> ResearchTask:
    return ResearchTask(
        task_id="task-1",
        parent_request_id="req-1",
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal="Understand the AI coding assistant market",
        focus="Market size and competitive positioning",
        company_profile=CompanyProfile(
            name="GitLab",
            products=["GitLab Duo"],
            notes="GitLab vs GitHub Copilot",
        ),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub Copilot", "Atlassian"],
            focus_areas=["AI-assisted development"],
        ),
    )


@pytest.fixture(autouse=True)
def _no_model_calls_from_guardrails_or_evaluation(monkeypatch):
    """Tests never reach a real model through the guardrail scope check or the evaluation judge."""
    from edrak.core.config import settings

    monkeypatch.setattr(settings, "GUARDRAIL_SCOPE_CHECK", False)
    monkeypatch.setattr(settings, "EVALUATION_JUDGE", False)


@pytest.fixture(autouse=True)
def _offline_metrics_agent():
    """The metrics agent never reaches the web or a model in tests unless a test plugs in its own seams."""
    from edrak.agents.metrics import nodes as agent
    from edrak.briefing.extract import Candidates

    agent.use(find=lambda p: Candidates(metrics=[]), search=lambda q: [], fetch=lambda u: "")
    yield
    agent.use(find=lambda p: Candidates(metrics=[]), search=lambda q: [], fetch=lambda u: "")
