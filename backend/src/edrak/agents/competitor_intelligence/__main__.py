if __name__ == "__main__" and __package__ is None:
    import runpy
    import sys
    from pathlib import Path

    # backend/src, so `edrak` can be imported when this file is run directly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    runpy.run_module("edrak.agents.competitor_intelligence", run_name="__main__")
    raise SystemExit(0)

from ...contracts.request import BusinessContext, BusinessRequest, CompanyProfile, TriggerType, UseCase
from ...contracts.task import ResearchTask, WorkerType
from . import run_research
if __name__ == "__main__":


    task = ResearchTask(
        task_id="task-123",
        parent_request_id="req-123",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Assess competitor AI capabilities for GitLab.",
        focus="Focus on GitHub and Azure DevOps AI workflows.",
        company_profile=CompanyProfile(
            name="GitLab",
            aliases=["GitLab Inc."],
            products=["GitLab Duo"],
        ),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=[
                "GitHub",
                "Microsoft Azure DevOps",
            ],
            focus_areas=[
                "AI coding",
                "agentic workflows",
            ],
            trigger=TriggerType.ON_DEMAND,
            constraints=[
                "Use evidence-backed competitor analysis",
            ],
        ),
    )


    run_research(
        task=task,
        print_result=True,
    )

