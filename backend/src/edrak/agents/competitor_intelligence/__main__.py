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

