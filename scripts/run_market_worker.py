"""Run the Market Intelligence agent against a ResearchTask."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT))

from edrak.agents.market_intelligence.graph import (
    format_task_context,
    get_graph,
    task_router,
    worker_result_from_state,
)
from edrak.core.llm import get_llm_client
from edrak.agents.market_intelligence.state import _banner, empty_market_state
from edrak.config import settings
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerResult,
    WorkerType,
    new_id,
    utcnow,
)

GOAL = (
"GitLab needs to know whether the market for a platform-wide agentic AI layer in DevSecOps is real enough to support building one across its product. Determine if enterprises are buying agents across plan, code, build, secure, and deploy, not only IDE coding assistants; what buyers require for that layer to be credible; how large and fast that category is growing; and which market conditions would make a single-platform agent layer realistic or unrealistic. Analyze six topics: how buyers and analysts define an agentic AI layer across the software lifecycle, and whether that category is distinct from coding assistants; TAM, growth, and spend for agentic AI in DevSecOps and the SDLC over the last 12–24 months; enterprise demand for lifecycle-wide agents versus IDE-only copilots, including who is buying and what use cases they fund; buyer requirements that make a platform layer credible, including governance, audit, data residency, model choice, and coverage of CI/CD and security, not just chat in the editor; how vendors price and package platform-wide AI agents (seats, credits, usage) as market norms, without product-vs-product comparison; and barriers that could make a platform-wide layer unrealistic, including regulation, procurement, trust, data-residency rules, and switching costs."    )

COMPANY = CompanyProfile(
    name="GitLab",
    aliases=["GitLab Inc.", "GitLab.com", "GitLab CI/CD", "GitLab DevSecOps"],
    products=[
        "GitLab Free",
        "GitLab Premium",
        "GitLab Ultimate",
        "GitLab Duo",
        "GitLab Duo Agent Platform",
        "GitLab Duo Self-Hosted",
        "GitLab Dedicated",
        "GitLab CI/CD",
        "AI Gateway",
        "GitLab Credits",
        "GitLab Orbit",
    ],
    notes=(
        "GitLab is an enterprise AI-powered DevSecOps platform delivered as a single application. "
        "Its strategic differentiators are a single data model across the software lifecycle, "
        "cloud and AI-model neutrality (multi-model routing via the AI Gateway, including self-hosted models), "
        "privacy and governance controls for AI, and a public, handbook-first operating culture. "
        "In 2026 it repositioned around the 'agentic era' (Act 2)."
    ),
)

BUSINESS_CONTEXT = BusinessContext(
    use_case=UseCase.PRODUCT_LAUNCH,
    targets=["GitHub", "Microsoft Azure DevOps"],
    focus_areas=[
        "AI coding",
        "agentic workflows",
        "pricing and packaging",
        "privacy and governance",
    ],
    constraints=[
        "Use evidence-backed analysis",
        "Compare GitLab Duo with competitor AI coding workflows",
    ],
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the EDRAK market intelligence agent")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Cap planned tasks after the planner. Omit this to run every planned task.",
    )
    parser.add_argument("--goal", default=GOAL)
    return parser.parse_args()


def build_task(args: argparse.Namespace) -> ResearchTask:
    return ResearchTask(
        parent_request_id=new_id(),
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal=args.goal,
        focus=(
            "Market viability of a platform-wide agentic AI layer in DevSecOps: category definition, TAM and growth, enterprise demand for lifecycle-wide agents versus IDE copilots, buyer requirements (governance, residency, CI/CD and security coverage), packaging norms, and adoption barriers — not competitor product monitoring."
        ),
        company_profile=COMPANY,
        business_context=BUSINESS_CONTEXT,
    )


def main() -> WorkerResult:
    args = parse_args()
    task = build_task(args)
    context_text = format_task_context(task)

    _banner("MARKET AGENT  --  STARTING")
    llm = get_llm_client()
    print(f"\n  LLM            : {llm.model}")
    print(f"  Endpoint       : {llm.base_url}")
    print(f"  Max tasks      : {args.max_tasks or 'all planned'}")
    print(f"\n  Goal:")
    print(f"    {task.goal}")
    print(f"\n  Business Context:")
    print(context_text)

    started_at = utcnow()
    initial_state = empty_market_state(
        run_id=task.parent_request_id,
        goal=task.goal,
        business_context=context_text,
    )

    if args.max_tasks:
        from edrak.agents.market_intelligence.nodes import fill_gaps, output_node, task_executor, task_planner

        state = dict(initial_state)
        state.update(task_planner(state))
        state["task_list"] = state["task_list"][: args.max_tasks]
        while True:
            nxt = task_router(state)
            if nxt == "task_executor":
                state.update(task_executor(state))
            elif nxt == "fill_gaps":
                state.update(fill_gaps(state))
            else:
                state.update(output_node(state))
                break
        result = worker_result_from_state(task, state, started_at=started_at)
    else:
        result = worker_result_from_state(task, get_graph().invoke(initial_state), started_at=started_at)

    reports_dir = ROOT / settings.artifacts_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"market_agent_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.json"
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    _banner("COMPLETE")
    print(f"  WorkerResult saved -> {out_path}\n")
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return result


if __name__ == "__main__":
    main()

"""
# mocked unit tests
python -m pytest tests -q

# full planned-task run
python scripts/run_market_worker.py

# one-task smoke
python scripts/run_market_worker.py --max-tasks 1
"""

