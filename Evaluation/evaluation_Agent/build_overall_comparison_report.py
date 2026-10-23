"""Aggregate dashboard-visible ground-truth comparison metrics."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


METRICS = ("mean_recall", "mean_alignment", "mean_f1", "pooled_recall")


def build_report(report_paths: list[Path]) -> dict[str, Any]:
    if len(report_paths) < 2:
        raise ValueError("Provide at least two compare.py reports to aggregate.")

    comparisons = []
    for path in report_paths:
        comparison = json.loads(path.read_text(encoding="utf-8"))
        overview = comparison.get("overview")
        if not isinstance(overview, dict):
            raise ValueError(f"{path} is not a compare.py report (missing overview).")
        comparisons.append({
            "ground_truth": comparison.get("ground_truth"),
            "run_bundle": comparison.get("run_bundle"),
            "method": comparison.get("method"),
            "metrics": {metric: overview.get(metric) for metric in METRICS},
        })

    averages = {}
    for metric in METRICS:
        values = [
            comparison["metrics"][metric]
            for comparison in comparisons
            if isinstance(comparison["metrics"][metric], (int, float))
        ]
        averages[metric] = round(statistics.mean(values), 3) if values else None

    return {
        "title": "Aggregate ground-truth comparison",
        "aggregation": "Unweighted macro-average across comparison reports.",
        "comparison_count": len(comparisons),
        "metrics": averages,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="Dashboard comparison JSON reports")
    parser.add_argument("--out", type=Path, required=True, help="Output JSON report")
    args = parser.parse_args()
    report = build_report(args.reports)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Comparison report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
