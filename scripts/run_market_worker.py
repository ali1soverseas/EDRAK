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

from edrak.agents.market_intelligence.graph import build_graph, format_task_context, worker_result_from_state
from edrak.agents.market_intelligence.state import MOCK_INPUT, _banner, empty_market_state
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the EDRAK market intelligence agent")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Cap planned tasks after the planner. Use 1 for a live smoke test.",
    )
    parser.add_argument("--goal", default=MOCK_INPUT["goal"])
    parser.add_argument("--context", default=MOCK_INPUT["business_context"])
    parser.add_argument("--company", default="GitLab")
    return parser.parse_args()


def build_task(args: argparse.Namespace) -> ResearchTask:
    return ResearchTask(
        parent_request_id=new_id(),
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal=args.goal,
        focus="Market size, demand, and growth opportunities",
        company_profile=CompanyProfile(
            name=args.company,
            notes=args.context,
        ),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            focus_areas=["market trends", "growth opportunities"],
        ),
    )


def main() -> WorkerResult:
    args = parse_args()
    task = build_task(args)
    context_text = format_task_context(task)

    _banner("MARKET AGENT  --  STARTING")
    print(f"\n  LLM            : {settings.llm_model}")
    print(f"  Max tasks      : {args.max_tasks or 'all planned'}")
    print(f"\n  Goal:")
    print(f"    {task.goal}")
    print(f"\n  Business Context:")
    print(f"    {context_text[:120]}...")

    started_at = utcnow()
    app = build_graph()
    initial_state = empty_market_state(
        run_id=task.parent_request_id,
        goal=task.goal,
        business_context=context_text,
    )

    if args.max_tasks:
        from edrak.agents.market_intelligence.nodes import output_node, task_executor, task_planner

        state = dict(initial_state)
        state.update(task_planner(state))
        state["task_list"] = state["task_list"][: args.max_tasks]
        while state["current_task_idx"] < len(state["task_list"]):
            state.update(task_executor(state))
        state.update(output_node(state))
        result = worker_result_from_state(task, state, started_at=started_at)
    else:
        result = worker_result_from_state(task, app.invoke(initial_state), started_at=started_at)

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

# live 1-task smoke
python scripts/run_market_worker.py --max-tasks 1

# full 4–7 task run (several minutes)
python scripts/run_market_worker.py
"""

