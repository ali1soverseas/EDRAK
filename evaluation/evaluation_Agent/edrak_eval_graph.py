#!/usr/bin/env python3
"""EDRAK evaluation agent as a LangGraph state machine.

Keep this file in the same folder as edrak_eval_agent.py (it reuses its evaluators as the agent's tools).

    START -> load_files -> build_context --(Send per file, parallel)--> evaluate_agent
                                                                             |
                                    +---------- retry missing (max 2) <-- audit
                                                                             |
                                                       pipeline_checks -> narrate -> write_reports -> END

Nodes
  load_files       read + detect each JSON (internal / competitor / verification / cross_signal / decision)
  build_context    shared context: goal, evidence, findings, signals; decides which files need evaluating
  evaluate_agent   one parallel worker per file (LangGraph Send); runs that agent's evaluator
  audit            coverage check: did every file get a result? if not, route back and retry the missing ones
  pipeline_checks  decision -> signal -> finding -> evidence traceability
  narrate          optional LLM (Groq if GROQ_API_KEY, else OpenAI) writes a short diagnosis from the numbers;
                   any figure it states that is not in the computed digest is flagged
  write_reports    CSVs, JSON reports, summary.md

House rule is unchanged: the LLM only produces judgements (faithfulness / relevancy scores, and the narrative).
Every count, percentage, pass/fail and flag is computed in Python.

    pip install langgraph deepeval python-dotenv            # + langchain-groq or langchain-openai for --narrative
    python edrak_eval_graph.py --inputs "outputs/*.json" --out reports
    python edrak_eval_graph.py --inputs "outputs/*.json" --dry-run      # no LLM at all
    python edrak_eval_graph.py --inputs "outputs/*.json" --narrative    # add the LLM diagnosis
    python edrak_eval_graph.py --inputs "outputs/*.json" --draw         # print the graph as Mermaid
"""
from __future__ import annotations

import argparse, json, operator, os, sys, threading
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
try:
    from langgraph.types import Send
except ImportError:  # older langgraph
    from langgraph.constants import Send

if __package__:
    from .edrak_eval_agent import (
        EVALUATORS,
        EvalAgent,
        Judge,
        build_ctx,
        detect,
        pipeline_checks,
        read_json,
        ungrounded_numbers,
    )
else:
    from edrak_eval_agent import (
        EVALUATORS,
        EvalAgent,
        Judge,
        build_ctx,
        detect,
        pipeline_checks,
        read_json,
        ungrounded_numbers,
    )

MAX_ATTEMPTS = 2
SKIP_KINDS = {"input", "orchestration"}


# ----------------------------------------------------------------------------- state
class State(TypedDict, total=False):
    files: list[dict]                                   # {path, kind, data}
    load_errors: list[dict]
    ctx: Any                                            # shared Ctx
    expected: list[str]                                 # file paths that must get a result
    results: Annotated[list[dict], operator.add]        # one per evaluated file (parallel writers)
    eval_errors: Annotated[list[dict], operator.add]
    attempts: int
    pipeline: dict
    narrative: str


class LockedJudge(Judge):
    """Evaluate nodes run in parallel threads; keep the on-disk cache consistent."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._lock = threading.Lock()

    def run(self, *a, **k):
        with self._lock:
            return super().run(*a, **k)


# ----------------------------------------------------------------------------- optional LLM for the narrative
def make_llm():
    try:
        if os.getenv("OPENAI_API_KEY"):
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    except ImportError as exc:
        print(f"narrative LLM unavailable ({exc}); skipping narrative.", file=sys.stderr)
    return None


NARRATIVE_PROMPT = (
    "You review the evaluation results of a multi-agent competitive-intelligence pipeline. "
    "You get a JSON digest of integrity checks, flags and metrics per agent. Write a concise diagnosis: "
    "(1) the 3-5 most serious problems, most serious first, naming the agent; "
    "(2) for each, the likely cause and one concrete fix. "
    "Use ONLY facts and numbers present in the digest. Do not invent or recompute figures. Under 250 words."
)


# ----------------------------------------------------------------------------- helpers
def group_results(results: list[dict], order: list[str]) -> dict[str, list[dict]]:
    seen, per_agent = set(), {}
    for res in sorted(results, key=lambda r: order.index(r["file_path"]) if r["file_path"] in order else 999):
        if res["file_path"] in seen:  # keep first result if a retry produced a duplicate
            continue
        seen.add(res["file_path"])
        per_agent.setdefault(res["kind"], []).append(res)
    return per_agent


def digest(per_agent: dict[str, list[dict]], pipeline: dict) -> dict:
    out: dict[str, Any] = {"agents": {}, "pipeline_checks": pipeline.get("checks", [])}
    for agent, results in per_agent.items():
        out["agents"][agent] = [{
            "file": r["file"],
            "checks_failed": [{"check": c["check"], "detail": c["detail"]} for c in r["checks"] if not c["passed"]],
            "checks_total": len(r["checks"]),
            "flag_counts": dict(Counter(f for u in r["rows"] for f in u.get("flags", []))),
            "metrics": {k: v for k, v in r["metrics"].items() if isinstance(v, (int, float, str)) and v is not None},
        } for r in results]
    return out


# ----------------------------------------------------------------------------- graph
def build_graph(args: argparse.Namespace):
    cfg = argparse.Namespace(f_thr=args.faithfulness_threshold, r_thr=args.relevancy_threshold, deep=args.deep)
    judge = LockedJudge(args.model, Path(args.cache), enabled=not args.dry_run)
    llm = make_llm() if (args.narrative and not args.dry_run) else None

    # ---- nodes
    def load_files(state: State) -> dict:
        files, errors = [], []
        for p in EvalAgent.expand(args.inputs):
            try:
                d = read_json(p)
                files.append({"path": str(p), "kind": detect(d), "data": d})
                print(f"  {p.name}: {files[-1]['kind']}")
            except Exception as exc:
                errors.append({"file": str(p), "error": f"{type(exc).__name__}: {exc}"})
                print(f"  ERROR {p}: {exc}", file=sys.stderr)
        return {"files": files, "load_errors": errors, "attempts": 0}

    def build_context(state: State) -> dict:
        loaded = [(Path(f["path"]), f["kind"], f["data"]) for f in state["files"]]
        ctx = build_ctx(loaded, args.task)
        if not ctx.goal:
            print("WARNING: no research goal found (include the initial_input file or pass --task).", file=sys.stderr)
        expected = [f["path"] for f in state["files"] if _evaluable(f["kind"])]
        for f in state["files"]:
            if f["kind"] not in SKIP_KINDS and f["kind"] not in EVALUATORS:
                print(f"Skipped {Path(f['path']).name}: unrecognised schema")
        return {"ctx": ctx, "expected": expected}

    def _evaluable(kind: str) -> bool:
        return kind in EVALUATORS and (not args.agents or kind in args.agents)

    def evaluate_agent(payload: dict) -> dict:
        """Worker for one file; receives the Send payload, not the whole state."""
        f, ctx = payload["file"], payload["ctx"]
        path = Path(f["path"])
        try:
            res = EVALUATORS[f["kind"]](path, f["data"], ctx, judge, cfg)
        except Exception as exc:  # recorded; audit decides whether to retry
            return {"eval_errors": [{"file": path.name, "file_path": f["path"], "error": f"{type(exc).__name__}: {exc}"}]}
        res.update(file=path.name, file_path=f["path"], kind=f["kind"])
        bad = [c for c in res["checks"] if not c["passed"]]
        print(f"[{f['kind']}] {path.name}: {len(res['rows'])} units, {len(res['checks']) - len(bad)}/{len(res['checks'])} checks passed")
        return {"results": [res]}

    def audit(state: State) -> dict:
        return {"attempts": state.get("attempts", 0) + 1}

    def pipeline_node(state: State) -> dict:
        per_agent = group_results(state.get("results", []), state["expected"])
        raw_decisions = [f["data"] for f in state["files"] if f["kind"] == "decision"]
        return {"pipeline": pipeline_checks(state["ctx"], per_agent, raw_decisions)}

    def narrate(state: State) -> dict:
        if llm is None:
            return {"narrative": ""}
        per_agent = group_results(state.get("results", []), state["expected"])
        dg = json.dumps(digest(per_agent, state["pipeline"]), ensure_ascii=False, default=str)
        try:
            text = str(llm.invoke([("system", NARRATIVE_PROMPT), ("human", dg)]).content).strip()
        except Exception as exc:
            print(f"narrative failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return {"narrative": ""}
        stray = ungrounded_numbers(text, [dg])
        if stray:
            text += f"\n\n_Note: figures not found in the computed digest, treat with caution: {', '.join(stray)}_"
        return {"narrative": text}

    def write_reports(state: State) -> dict:
        per_agent = group_results(state.get("results", []), state["expected"])
        if not per_agent:
            print("ERROR: nothing was evaluated.", file=sys.stderr)
            return {}
        agent = EvalAgent(args)
        agent.judge, agent.per_agent, agent.pipeline = judge, per_agent, state["pipeline"]
        agent.errors = state.get("load_errors", []) + state.get("eval_errors", [])
        agent.write_all()
        if state.get("narrative"):
            md_path = Path(args.out) / "summary.md"
            md = md_path.read_text(encoding="utf-8")
            md = md.replace("# EDRAK evaluation summary\n",
                            "# EDRAK evaluation summary\n\n## Diagnosis (LLM-written from the computed results)\n\n"
                            + state["narrative"] + "\n", 1)
            md_path.write_text(md, encoding="utf-8")
        return {}

    # ---- edges
    def fan_out(state: State):
        """Parallel fan-out: one evaluate_agent run per evaluable file."""
        sends = [Send("evaluate_agent", {"file": f, "ctx": state["ctx"]})
                 for f in state["files"] if f["path"] in state["expected"]]
        return sends or END

    def after_audit(state: State):
        """Loop back for files that produced no result, up to MAX_ATTEMPTS."""
        done = {r["file_path"] for r in state.get("results", [])}
        missing = [p for p in state["expected"] if p not in done]
        if missing and state.get("attempts", 0) < MAX_ATTEMPTS:
            print(f"audit: {len(missing)} file(s) without a result, retrying (attempt {state['attempts'] + 1})")
            return [Send("evaluate_agent", {"file": f, "ctx": state["ctx"]})
                    for f in state["files"] if f["path"] in missing]
        if missing:
            print(f"audit: giving up on {len(missing)} file(s); see eval_errors", file=sys.stderr)
        return "pipeline_checks"

    g = StateGraph(State)
    g.add_node("load_files", load_files)
    g.add_node("build_context", build_context)
    g.add_node("evaluate_agent", evaluate_agent)
    g.add_node("audit", audit)
    g.add_node("pipeline_checks", pipeline_node)
    g.add_node("narrate", narrate)
    g.add_node("write_reports", write_reports)

    g.add_edge(START, "load_files")
    g.add_edge("load_files", "build_context")
    g.add_conditional_edges("build_context", fan_out, ["evaluate_agent", END])
    g.add_edge("evaluate_agent", "audit")
    g.add_conditional_edges("audit", after_audit, ["evaluate_agent", "pipeline_checks"])
    g.add_edge("pipeline_checks", "narrate")
    g.add_edge("narrate", "write_reports")
    g.add_edge("write_reports", END)
    return g.compile(), judge


# ----------------------------------------------------------------------------- cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--out", default="reports")
    ap.add_argument("--agents", nargs="+", choices=sorted(EVALUATORS), help="Only evaluate these agents")
    ap.add_argument("--task", default="", help="Override the research goal text")
    ap.add_argument("--model", default="gpt-4.1-mini", help="deepeval judge model")
    ap.add_argument("--faithfulness-threshold", type=float, default=0.8)
    ap.add_argument("--relevancy-threshold", type=float, default=0.7)
    ap.add_argument("--cache", default="edrak_judge_cache.json")
    ap.add_argument("--dry-run", action="store_true", help="No LLM calls; deterministic checks only")
    ap.add_argument("--deep", action="store_true", help="Also judge each recommended action")
    ap.add_argument("--narrative", action="store_true", help="Generate a narrative summary report")
    # ap.add_argument("--draw", action="store_true", help="Print the graph as Mermaid and exit")
    a = ap.parse_args()

    if not a.dry_run and not a.draw:
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
        except ImportError:
            pass
        if not os.getenv("OPENAI_API_KEY"):  # deepeval judge
            print("ERROR: set OPENAI_API_KEY for the deepeval judge (or use --dry-run).", file=sys.stderr)
            return 2

    graph, judge = build_graph(a)
    if a.draw:
        print(graph.get_graph().draw_mermaid())
        return 0
    final = graph.invoke({})
    if not final.get("results"):
        return 2
    print(f"LLM judge calls: {judge.calls}, cache hits: {judge.hits}")
    return 1 if (final.get("load_errors") or final.get("eval_errors")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
