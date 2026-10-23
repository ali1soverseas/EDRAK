"""Build a cross-use-case performance summary from compare.py JSON reports."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


METRICS = (
    "mean_recall",
    "pooled_recall",
    "mean_graded_recall",
    "mean_weighted_recall",
    "must_recall",
    "mean_alignment",
    "mean_precision_adjusted",
    "mean_f1",
    "mean_f1_adjusted",
    "contradiction_rate",
    "noise_rate",
)
LABELS = {
    "nvidia_competitive_intelligence_inputs": "Competitive intelligence",
    "nvidia_new_markets_inputs": "Market entry",
    "nvidia_product_launch_inputs": "Product launch",
}


def _flat_stages(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stages = {f"workers/{name}": value for name, value in report.get("workers", {}).items()}
    stages.update({
        name: report[name]
        for name in ("verification", "cross_signal")
        if isinstance(report.get(name), dict)
    })
    stages.update({
        f"decision/{name}": value
        for name, value in report.get("decision", {}).items()
        if isinstance(value, dict)
    })
    return stages


def build_report(
    report_paths: list[Path],
    ground_truth_root: Path,
    evaluation_paths: list[Path],
) -> dict[str, Any]:
    if len(report_paths) < 2:
        raise ValueError("Provide at least two compare.py reports to aggregate.")
    if evaluation_paths and len(evaluation_paths) != len(report_paths):
        raise ValueError("Provide one orchestration evaluation report for each comparison report.")

    cases = []
    for index, path in enumerate(report_paths):
        comparison = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(comparison.get("overview"), dict):
            raise ValueError(f"{path} is not a compare.py report (missing overview).")
        name = str(comparison.get("ground_truth") or "")
        truth_meta_path = ground_truth_root / name / "ground_truth.json"
        truth_meta = json.loads(truth_meta_path.read_text(encoding="utf-8")) if truth_meta_path.is_file() else {}
        stages = _flat_stages(comparison)
        execution: dict[str, Any] = {}
        if evaluation_paths:
            evaluation = json.loads(evaluation_paths[index].read_text(encoding="utf-8"))
            orchestration = next(
                (
                    item.get("metrics") or {}
                    for item in evaluation.get("files", [])
                    if item.get("agent") == "orchestration" or (item.get("metrics") or {}).get("planned_tasks") is not None
                ),
                {},
            )
            if not orchestration:
                raise ValueError(f"{evaluation_paths[index]} contains no orchestration metrics.")
            execution = {
                key: orchestration.get(key)
                for key in (
                    "planned_tasks", "successful_tasks", "task_completion_pct",
                    "workflow_success_pct", "failed_tasks", "partial_tasks", "incomplete_tasks", "retry_events",
                    "successfully_retried_tasks", "retry_rate_pct",
                )
            }
        scored_stages = [
            (stage, values.get("mean_recall", values.get("graded_recall")))
            for stage, values in stages.items()
            if isinstance(values.get("mean_recall", values.get("graded_recall")), (int, float))
        ]
        cases.append({
            "ground_truth": name,
            "label": LABELS.get(name, name.replace("_", " ").title()),
            "run_bundle": comparison.get("run_bundle"),
            "method": comparison.get("method"),
            "curated": truth_meta.get("curated"),
            "curation_status": truth_meta.get("curation_status"),
            "curation_note": truth_meta.get("note"),
            "known_limitations": truth_meta.get("known_limitations", []),
            "overview": {metric: comparison["overview"].get(metric) for metric in METRICS},
            "execution": execution,
            "query_alignment": comparison.get("query", {}),
            "orchestrator": comparison.get("orchestrator", {}),
            "strongest_stages": [
                name for name, _ in sorted(scored_stages, key=lambda item: item[1], reverse=True)[:3]
            ],
            "weakest_stages": [
                name for name, _ in sorted(scored_stages, key=lambda item: item[1])[:3]
            ],
            "stages_with_zero_recall": comparison["overview"].get("stages_with_zero_recall", []),
            "stages_with_contradictions": comparison["overview"].get("stages_with_contradictions", []),
        })

    averages: dict[str, float | None] = {}
    for metric in METRICS:
        values = [case["overview"][metric] for case in cases if isinstance(case["overview"][metric], (int, float))]
        averages[metric] = round(statistics.mean(values), 3) if values else None
    comparison_methods = sorted({
        str(case["method"])
        for case in cases
        if case.get("method")
    })
    comparison_method = (
        f"compare.py {comparison_methods[0]}"
        if len(comparison_methods) == 1
        else "compare.py mixed methods: " + "; ".join(comparison_methods)
    )
    execution_cases = [case["execution"] for case in cases if case["execution"]]
    planned_total = sum(item.get("planned_tasks") or 0 for item in execution_cases)
    successful_total = sum(item.get("successful_tasks") or 0 for item in execution_cases)
    execution_summary = {
        "mean_task_completion_pct": (
            round(statistics.mean(item["task_completion_pct"] for item in execution_cases
                                  if isinstance(item.get("task_completion_pct"), (int, float))), 2)
            if any(isinstance(item.get("task_completion_pct"), (int, float)) for item in execution_cases) else None
        ),
        "pooled_task_completion_pct": round(100 * successful_total / planned_total, 2) if planned_total else None,
        "mean_workflow_success_pct": (
            round(statistics.mean(item["workflow_success_pct"] for item in execution_cases
                                  if isinstance(item.get("workflow_success_pct"), (int, float))), 2)
            if any(isinstance(item.get("workflow_success_pct"), (int, float)) for item in execution_cases) else None
        ),
        "mean_retry_rate_pct": (
            round(statistics.mean(item["retry_rate_pct"] for item in execution_cases
                                  if isinstance(item.get("retry_rate_pct"), (int, float))), 2)
            if any(isinstance(item.get("retry_rate_pct"), (int, float)) for item in execution_cases) else None
        ),
        "successful_tasks": successful_total,
        "planned_tasks": planned_total,
        "failed_tasks": sum(item.get("failed_tasks") or 0 for item in execution_cases),
        "partial_tasks": sum(item.get("partial_tasks") or 0 for item in execution_cases),
        "incomplete_tasks": sum(item.get("incomplete_tasks") or 0 for item in execution_cases),
        "retry_events": sum(item.get("retry_events") or 0 for item in execution_cases),
        "successfully_retried_tasks": sum(item.get("successfully_retried_tasks") or 0 for item in execution_cases),
    }

    strongest = max(
        cases,
        key=lambda case: (
            case["overview"]["mean_recall"]
            if isinstance(case["overview"]["mean_recall"], (int, float)) else -1
        ),
    )
    weakest = min(
        cases,
        key=lambda case: (
            case["overview"]["mean_recall"]
            if isinstance(case["overview"]["mean_recall"], (int, float)) else 2
        ),
    )
    score_pct = lambda value: f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "not available"
    interpretations = [
        f"Across {len(cases)} use cases, macro-average recall is {score_pct(averages['mean_recall'])}; "
        f"mean graded recall is {score_pct(averages['mean_graded_recall'])}.",
        f"{strongest['label']} has the highest mean recall ({score_pct(strongest['overview']['mean_recall'])}); "
        f"{weakest['label']} has the lowest ({score_pct(weakest['overview']['mean_recall'])}).",
    ]
    zero_recall = sorted({
        stage for case in cases for stage in case["stages_with_zero_recall"]
    })
    if zero_recall:
        interpretations.append("Stages with zero recall in at least one comparison: " + ", ".join(zero_recall) + ".")
    for case in cases:
        if case["strongest_stages"] or case["weakest_stages"]:
            strongest_names = ", ".join(case["strongest_stages"]) or "none scored"
            weakest_names = ", ".join(case["weakest_stages"]) or "none scored"
            interpretations.append(
                f"{case['label']}: strongest stages were {strongest_names}; weakest were {weakest_names}."
            )
    if averages["noise_rate"] is not None:
        interpretations.append(
            f"Average noise rate is {score_pct(averages['noise_rate'])}; review off-reference findings "
            "for relevance rather than treating every extra as incorrect."
        )
    if execution_cases:
        interpretations.append(
            f"Orchestration completed {successful_total} of {planned_total} planned tasks "
            f"({score_pct(execution_summary['pooled_task_completion_pct'] / 100 if execution_summary['pooled_task_completion_pct'] is not None else None)} "
            f"pooled task completion); mean workflow-success status was "
            f"{execution_summary['mean_workflow_success_pct']:.1f}%."
        )
        interpretations.append(
            f"{execution_summary['retry_events']} retry/replan event(s) were observed; "
            f"{execution_summary['successfully_retried_tasks']} retried task(s) ultimately completed successfully."
        )

    caveats = [
        (
            "LLM-judged comparisons are model-based and may vary; treat semantic matches and scores as approximate."
            if any("llm judge" in method.lower() for method in comparison_methods)
            else "Word-overlap comparisons are approximate and may not capture semantic equivalence."
        ),
        "Ground-truth datasets are not fully curated; results are directional, not benchmark certification.",
    ]
    for case in cases:
        if case["curation_status"] or case["curated"] is False:
            status = case["curation_status"] or "uncurated"
            caveats.append(f"{case['label']} ground truth is {status}.")

    return {
        "title": "Overall performance across ground-truth comparisons",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "comparison_method": comparison_method,
        "aggregation": "Unweighted macro-average across the three use-case comparison reports.",
        "case_count": len(cases),
        "overall": averages,
        "execution": execution_summary,
        "interpretation": interpretations,
        "caveats": list(dict.fromkeys(caveats)),
        "cases": cases,
    }


def markdown_report(report: dict[str, Any]) -> str:
    as_pct = lambda value: f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "n/a"
    metrics = report["overall"]
    lines = [
        f"# {report['title']}",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        f"Method: {report['comparison_method']}",
        "",
        report["aggregation"],
        "",
        "## Overall averages",
        "",
        "| Metric | Average |",
        "|---|---:|",
    ]
    for metric, label in (
        ("mean_recall", "Mean recall"),
        ("pooled_recall", "Average pooled recall"),
        ("mean_graded_recall", "Mean graded recall"),
        ("mean_weighted_recall", "Mean weighted recall"),
        ("must_recall", "Must-item recall"),
        ("mean_alignment", "Mean alignment"),
        ("mean_f1", "Mean F1"),
        ("noise_rate", "Mean noise rate"),
        ("contradiction_rate", "Mean contradiction rate"),
    ):
        lines.append(f"| {label} | {as_pct(metrics.get(metric))} |")
    execution = report.get("execution") or {}
    if execution:
        lines += ["", "## Orchestration execution", "",
                  f"- Successful tasks: {execution['successful_tasks']} / {execution['planned_tasks']} "
                  f"({execution['pooled_task_completion_pct']}% pooled task completion).",
                  f"- Mean per-run task completion: {execution['mean_task_completion_pct']}%.",
                  f"- Mean workflow success: {execution['mean_workflow_success_pct']}%.",
                  f"- Partial worker outputs: {execution['partial_tasks']}; failed tasks: {execution['failed_tasks']}; "
                  f"incomplete tasks: {execution['incomplete_tasks']}.",
                  f"- Retry/replan events: {execution['retry_events']}; successfully completed retried tasks: "
                  f"{execution['successfully_retried_tasks']}.",
                  "", "## Per-use-case comparisons", "",
                  "| Use case | Ground truth | Recall | Graded | Alignment | F1 | Noise | Task completion | Workflow success | Successful retry rate | Strongest stages | Weakest stages |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    else:
        lines += ["", "## Per-use-case comparisons", "",
                  "| Use case | Ground truth | Mean recall | Graded recall | Alignment | F1 | Noise |",
                  "|---|---|---:|---:|---:|---:|---:|"]
    for case in report["cases"]:
        overview = case["overview"]
        row = (
            f"| {case['label']} | {case['curation_status'] or ('curated' if case['curated'] else 'uncurated')} "
            f"| {as_pct(overview.get('mean_recall'))} | {as_pct(overview.get('mean_graded_recall'))} "
            f"| {as_pct(overview.get('mean_alignment'))} | {as_pct(overview.get('mean_f1'))} "
            f"| {as_pct(overview.get('noise_rate'))}"
        )
        if execution:
            row += (
                f" | {case['execution'].get('task_completion_pct', 'n/a')}%"
                f" | {case['execution'].get('workflow_success_pct', 'n/a')}%"
                f" | {case['execution'].get('retry_rate_pct', 'n/a')}%"
            )
        row += (
            f" | {', '.join(case['strongest_stages']) or 'n/a'}"
            f" | {', '.join(case['weakest_stages']) or 'n/a'}"
        )
        lines.append(row + " |")
    lines += ["", "## Interpretation", ""] + [f"- {item}" for item in report["interpretation"]]
    lines += ["", "## Caveats", ""] + [f"- {item}" for item in report["caveats"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="Per-run JSON reports produced by compare.py")
    parser.add_argument(
        "--evaluation-reports",
        nargs="*",
        type=Path,
        default=[],
        help="Matching per-run orchestration_report.json files, in the same order as reports",
    )
    parser.add_argument("--ground-truth-root", type=Path, default=Path("golden/ground_truth"))
    parser.add_argument("--out", type=Path, required=True, help="Output JSON report for the evaluation frontend")
    args = parser.parse_args()
    report = build_report(args.reports, args.ground_truth_root, args.evaluation_reports)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    print(f"Overall report: {args.out}")
    print(f"Readable report: {args.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
