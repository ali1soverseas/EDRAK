"""Run the Market Intelligence agent against a ResearchTask."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT))

from edrak.agents.market_intelligence.graph import build_graph, worker_result_from_state
from edrak.agents.market_intelligence.state import MOCK_INPUT, _banner, empty_market_state
from edrak.config import settings
from edrak.contracts import ResearchTask, WorkerResult


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
    return parser.parse_args()


def main() -> WorkerResult:
    args = parse_args()
    _banner("MARKET AGENT  --  STARTING")
    print(f"\n  LLM            : {settings.llm_model}")
    print(f"  Max tasks      : {args.max_tasks or 'all planned'}")
    print(f"\n  Goal:")
    print(f"    {args.goal}")
    print(f"\n  Business Context:")
    print(f"    {args.context[:120]}...")

    task = ResearchTask(
        task_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        worker="market",
        goal=args.goal,
        business_context=args.context,
    )

    app = build_graph()
    initial_state = empty_market_state(
        run_id=task.run_id,
        goal=task.goal,
        business_context=task.business_context,
    )

    if args.max_tasks:
        from edrak.agents.market_intelligence.nodes import output_node, task_executor, task_planner

        state = dict(initial_state)
        state.update(task_planner(state))
        state["task_list"] = state["task_list"][: args.max_tasks]
        while state["current_task_idx"] < len(state["task_list"]):
            state.update(task_executor(state))
        state.update(output_node(state))
        result = worker_result_from_state(task, state)
    else:
        result = worker_result_from_state(task, app.invoke(initial_state))

    reports_dir = ROOT / settings.artifacts_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"market_agent_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.json"
    out_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    _banner("COMPLETE")
    print(f"  WorkerResult saved -> {out_path}\n")
    print(json.dumps(result.model_dump(), indent=2))
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
