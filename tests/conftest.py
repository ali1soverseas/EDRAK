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
