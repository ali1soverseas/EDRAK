#!/usr/bin/env python3
"""LangGraph entry point for the enhanced EDRAK Evaluation Agent.

The evaluation logic lives in edrak_eval_agent_enhanced.py so the CLI and graph
runner share exactly the same evaluators, RAG checks, orchestration checks,
judge cache, and observability reports.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

try:
    from .edrak_eval_agent_enhanced import EvalAgent
except ImportError:
    from edrak_eval_agent_enhanced import EvalAgent


class State(TypedDict, total=False):
    exit_code: int
    error: str


def build_graph(args: argparse.Namespace):
    def run_evaluation(state: State) -> dict:
        agent = EvalAgent(args)
        if not agent.load():
            return {"exit_code": 2, "error": f"No readable files matched {args.inputs}"}
        return {"exit_code": agent.run()}

    graph = StateGraph(State)
    graph.add_node("run_evaluation", run_evaluation)
    graph.add_edge(START, "run_evaluation")
    graph.add_edge("run_evaluation", END)
    return graph.compile()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", nargs="+", required=True, help="JSON files/globs from one research run")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--agents", nargs="+", help="Kinds or worker names to evaluate")
    ap.add_argument("--task", default="", help="Override research goal")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    ap.add_argument("--workers", type=int, default=1, help="Parallel judge calls per unit (1 = sequential)")
    ap.add_argument("--retries", type=int, default=2, help="Attempts per judge metric before recording a failure")
    ap.add_argument("--faithfulness-threshold", type=float, default=0.7)
    ap.add_argument("--relevancy-threshold", type=float, default=0.5)
    ap.add_argument("--context-relevancy-threshold", type=float, default=0.5)
    ap.add_argument("--specificity-threshold", type=float, default=0.5)
    ap.add_argument("--insight-threshold", type=float, default=0.5)
    ap.add_argument("--actionability-threshold", type=float, default=0.5)
    ap.add_argument("--context-relevancy", action="store_true")
    metric_group = ap.add_mutually_exclusive_group()
    metric_group.add_argument("--extra-metrics", dest="extra_metrics", action="store_true")
    metric_group.add_argument("--no-extra-metrics", dest="extra_metrics", action="store_false",
                              help=argparse.SUPPRESS)
    ap.set_defaults(extra_metrics=False)
    ap.add_argument("--cache", default="edrak_judge_cache.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--narrative", action="store_true")
    ap.add_argument(
        "--include-tool-payloads",
        action="store_true",
        help="Send redacted tool arguments/results to DeepEval for argument and result-use judgments",
    )
    ap.add_argument("--draw", action="store_true", help="Print the LangGraph topology")
    args = ap.parse_args()

    if args.draw:
        print(build_graph(args).get_graph().draw_mermaid())
        return 0
    if not args.dry_run:
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
        except ImportError:
            pass
        if not os.getenv("OPENAI_API_KEY"):
            print("ERROR: set OPENAI_API_KEY for the DeepEval judge (or use --dry-run).", file=sys.stderr)
            return 2
        try:
            import deepeval  # noqa: F401
        except ImportError:
            print("ERROR: pip install deepeval (or use --dry-run).", file=sys.stderr)
            return 2

    result = build_graph(args).invoke({})
    if result.get("error"):
        print(result["error"], file=sys.stderr)
    return int(result.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(main())
