"""Mock orchestrator for the competitive-intelligence slice.

Runs internal, market, and competitor workers on one BusinessRequest, then
passes their WorkerResults to verification.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

BACKEND_SRC = ROOT / "backend" / "src"
for path in (BACKEND_SRC, ROOT):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from edrak.agents.competitor_intelligence import run_research
from edrak.agents.internal_intelligence.graph import run_internal_intelligence
from edrak.agents.market_intelligence.graph import run as run_market_intelligence
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    ResearchTask,
    TriggerType,
    UseCase,
    VerificationInput,
    VerificationResult,
    WorkerResult,
    WorkerType,
)
from edrak.core.profiles import get_gitlab_profile
from edrak.verification.graph import run as run_verification


GOAL = (
    "Assess how GitLab should respond to competitor AI-assisted development "
    "capabilities, especially GitHub and Microsoft Azure DevOps."
)
BUSINESS_CONTEXT = BusinessContext(
    use_case=UseCase.COMPETITIVE_INTELLIGENCE,
    targets=["GitHub", "Microsoft Azure DevOps"],
    focus_areas=[
        "AI coding",
        "agentic workflows",
        "pricing and packaging",
        "privacy and governance",
    ],
    trigger=TriggerType.ON_DEMAND,
    constraints=[
        "Use evidence-backed analysis",
        "Compare GitLab Duo with competitor AI coding workflows",
    ],
)
TASKS = (
    (
        WorkerType.INTERNAL_INTELLIGENCE,
        "Assess GitLab Duo product architecture, packaging, and internal position versus GitHub Copilot and Azure DevOps.",
        "GitLab Duo Agent Platform, AI Gateway, zero-retention data privacy, GitLab Credits, and self-hosted readiness.",
    ),
    (
        WorkerType.MARKET_INTELLIGENCE,
        "Understand current demand and growth opportunities for AI-assisted DevSecOps platforms.",
        "Market size, demand, and growth opportunities for AI coding and agentic development workflows.",
    ),
    (
        WorkerType.COMPETITOR_INTELLIGENCE,
        "Assess competitor AI capabilities for GitLab.",
        "GitHub and Microsoft Azure DevOps AI coding and agentic workflows.",
    ),
)


def build_request() -> BusinessRequest:
    return BusinessRequest(
        goal=GOAL,
        company_profile=get_gitlab_profile(),
        business_context=BUSINESS_CONTEXT,
    )


def build_tasks(request: BusinessRequest) -> list[ResearchTask]:
    return [
        ResearchTask(
            parent_request_id=request.request_id,
            worker=worker,
            goal=goal,
            focus=focus,
            company_profile=request.company_profile,
            business_context=request.business_context,
        )
        for worker, goal, focus in TASKS
    ]


def run_worker(task: ResearchTask) -> WorkerResult:
    if task.worker is WorkerType.INTERNAL_INTELLIGENCE:
        return run_internal_intelligence(task)
    if task.worker is WorkerType.MARKET_INTELLIGENCE:
        return run_market_intelligence(task)
    if task.worker is WorkerType.COMPETITOR_INTELLIGENCE:
        return run_research(task, print_result=False)
    raise ValueError(f"Unsupported worker: {task.worker}")


def _write(path: Path, payload) -> None:
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


def _print_verification(verification: VerificationResult) -> None:
    decision = verification.decision
    print("\n" + "=" * 72)
    print("VERIFICATION RESULT")
    print("=" * 72)
    print(f"Run:      {verification.research_run_id}")
    print(f"Status:   {decision.status.value}")
    print(f"Summary:  {decision.summary}")
    print(
        "Findings: "
        f"{verification.metadata.get('verified_count', 0)} verified, "
        f"{verification.metadata.get('insufficient_count', 0)} insufficient, "
        f"{verification.metadata.get('finding_count', len(verification.findings))} total"
    )
    if verification.control_summary.missing_information:
        print("Missing information:")
        for item in verification.control_summary.missing_information:
            print(f"  - {item}")
    if verification.control_summary.conflicts:
        print("Contradictions:")
        for item in verification.control_summary.conflicts:
            print(f"  - {item}")
    if decision.targeted_actions:
        print("Orchestrator actions:")
        for action in decision.targeted_actions:
            print(f"  - {action.worker.value}: {action.reason}")


def _verify(request: BusinessRequest, results: list[WorkerResult], out_dir: Path, name: str) -> VerificationResult:
    verification_input = VerificationInput(request=request, agent_outputs=results)
    _write(out_dir / f"{name}_input.json", verification_input)
    verification = run_verification(verification_input)
    _write(out_dir / f"{name}.json", verification)
    _print_verification(verification)
    print(f"Saved:    {out_dir / f'{name}.json'}")
    return verification


def main() -> None:
    request = build_request()
    tasks = build_tasks(request)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_dir = ROOT / "artifacts" / "reports" / f"mock_orchestrator_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "business_request.json", request)

    print("=" * 72)
    print("MOCK ORCHESTRATOR")
    print("=" * 72)
    print(f"Request:  {request.request_id}")
    print(f"Company:  {request.company_profile.name}")
    print(f"Goal:     {request.goal}")
    print(f"Targets:  {', '.join(request.business_context.targets)}")
    print(f"Output:   {out_dir}")

    results: list[WorkerResult] = []
    for task in tasks:
        print("\n" + "-" * 72)
        print(f"WORKER  {task.worker.value}")
        print(f"Task    {task.task_id}")
        print(f"Goal    {task.goal}")
        result = run_worker(task)
        results.append(result)
        _write(out_dir / f"{task.worker.value}.json", result)
        print(
            f"Status  {result.status.value} | "
            f"findings {len(result.findings)} | evidence {len(result.evidence)}"
        )

    verification = _verify(request, results, out_dir, "verification_result")
    print(json.dumps(verification.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
