"""Run the worker on the golden brief and score it with the evaluation suite.

Needs the judge package once: uv pip install --python .venv/bin/python deepeval
Run from the repository root:
    .venv/bin/python scripts/customer_trends/eval_golden.py --label baseline --runs 3

Each run is a live run of backend/evals/customer_trends/briefs/ai_accelerators.json under a new
run id, with the evaluation trace switched on. The trace is scored by Evaluation/generic/evaluator.py
against Evaluation/golden/customer_trends_ground_truth_single_case.json (an Ollama judge, see
OLLAMA_MODEL, OLLAMA_BASE_URL and OLLAMA_API_KEY in .env). Reports go to
reports/customer_trends/<label>/run<N>/; the mean over the runs is printed and saved as
summary.json. Runs are live: they spend provider quota and judge calls. Variables set in the shell
(for example SERPER_API_KEY) win over .env.
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BRIEF = ROOT / "backend/evals/customer_trends/briefs/ai_accelerators.json"
EVALUATOR = ROOT / "Evaluation/generic/evaluator.py"
GOLDEN = ROOT / "Evaluation/golden/customer_trends_ground_truth_single_case.json"
REPORTS = ROOT / "reports/customer_trends"
QUALITY = ("relevancy", "correctness", "faithfulness", "completeness")


def read_env(path: Path) -> dict[str, str]:
    """The variables of a .env file. Values are never printed."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not match:
            continue
        value = re.sub(r"\s+#.*$", "", match.group(2)).strip().strip("\"'")
        if value:
            values[match.group(1)] = value
    return values


def run_worker(
    label: str, number: int, out_dir: Path, env: dict[str, str]
) -> Path | None:
    """One live run under a fresh run id. Returns the path of its evaluation trace."""
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    run_id = f"run-{label}-{int(time.time())}-{number}"
    brief["run_id"], brief["task_id"] = run_id, f"task-{run_id}"
    brief_path = out_dir / "brief.json"
    brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    command = [
        str(ROOT / ".venv/bin/customer-trends"),
        "run",
        "--brief",
        str(brief_path),
        "--out",
        str(out_dir / "result.json"),
    ]
    done = subprocess.run(
        command, env={**env, "EVAL_TRACE_ENABLED": "true"}, cwd=ROOT, check=False
    )
    trace = ROOT / "artifacts/runs" / run_id / "customer_trends/eval_trace.json"
    if not trace.is_file():
        print(f"run {number}: no trace written (worker exit code {done.returncode})")
        return None
    (out_dir / "eval_trace.json").write_bytes(trace.read_bytes())
    return trace


def score(
    trace: Path, out_dir: Path, env: dict[str, str], second_pass: bool
) -> dict[str, Any] | None:
    command = [
        sys.executable,
        str(EVALUATOR),
        "--trace",
        str(trace),
        "--ground-truth",
        str(GOLDEN),
        "--out-dir",
        str(out_dir),
    ]
    if not second_pass:
        command.append("--no-second-pass")
    subprocess.run(command, env=env, cwd=ROOT, check=False)
    report = out_dir / "customer_trends_evaluation.json"
    return json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    quality = (report.get("worker_result_quality") or {}).get("metrics") or {}
    return {
        "coverage": report.get("coverage_score"),
        "strict_coverage": report.get("strict_coverage_score"),
        "quality": {name: (quality.get(name) or {}).get("score") for name in QUALITY},
        "findings": {
            f["finding_id"]: f.get("final_status")
            for f in report.get("finding_results", [])
        },
        "scores": {
            f["finding_id"]: f.get("final_score")
            for f in report.get("finding_results", [])
        },
        "findings_written": (report.get("deterministic_checks") or {}).get(
            "finding_count"
        ),
    }


def mean(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(statistics.fmean(known), 4) if known else None


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ids = sorted({fid for run in runs for fid in run["scores"]})
    return {
        "runs": len(runs),
        "coverage": mean([r["coverage"] for r in runs]),
        "strict_coverage": mean([r["strict_coverage"] for r in runs]),
        "coverage_each": [r["coverage"] for r in runs],
        "quality": {q: mean([r["quality"][q] for r in runs]) for q in QUALITY},
        "mean_score_by_finding": {
            fid: mean([r["scores"].get(fid) for r in runs]) for fid in ids
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--label", required=True, help="name of this series, for example baseline"
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--second-pass",
        action="store_true",
        help="also run the evaluator's second pass",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="score the traces already in reports/customer_trends/<label>/ without new runs",
    )
    args = parser.parse_args()
    env = {**read_env(ROOT / ".env"), **os.environ}  # the shell wins over .env
    summaries: list[dict[str, Any]] = []
    for number in range(1, args.runs + 1):
        out_dir = REPORTS / args.label / f"run{number}"
        out_dir.mkdir(parents=True, exist_ok=True)
        trace = out_dir / "eval_trace.json"
        if not (args.rescore and trace.is_file()):
            trace = run_worker(args.label, number, out_dir, env)
        report = score(trace, out_dir, env, args.second_pass) if trace else None
        if report is None:
            print(f"run {number}: not scored")
            continue
        summary = summarize(report)
        summaries.append(summary)
        print(f"run {number}: {json.dumps(summary)}")
    if not summaries:
        return 1
    total = aggregate(summaries)
    (REPORTS / args.label / "summary.json").write_text(
        json.dumps({"mean": total, "runs": summaries}, indent=2), encoding="utf-8"
    )
    print("mean:", json.dumps(total, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
