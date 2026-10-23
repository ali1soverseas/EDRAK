from __future__ import annotations

import json
from pathlib import Path

from Evaluation.evaluation_Agent.build_overall_comparison_report import build_report
from Evaluation.evaluation_Agent.compare import coverage_overview, merge_verdicts, score


class StubJudge:
    def match(self, label, truth, system):
        return [
            {"verdict": "full", "system": [0], "reason": "matched"}
            if index == 0 and system
            else {"verdict": "none", "system": [], "reason": "not matched"}
            for index, _ in enumerate(truth)
        ]

    def rescue(self, label, truth, system, candidates):
        return {}


def test_comparison_score_exports_only_dashboard_fields() -> None:
    result = score(
        {
            "judge": StubJudge(),
            "consistency": False,
        },
        "worker findings",
        [
            {"statement": "GitLab supports AI-assisted code completion"},
            {"statement": "GitHub offers cloud hosting"},
        ],
        [{"statement": "GitLab supports AI-assisted code completion"}],
        ("statement",),
    )

    assert set(result) == {
        "truth_items", "matched", "recall", "alignment", "f1", "volume_ratio", "not_covered"
    }
    assert result["recall"] == 0.5
    assert result["alignment"] == 1.0
    assert result["f1"] == 0.667
    assert result["volume_ratio"] == 0.5
    assert len(result["not_covered"]) == 1


def test_consistency_merge_keeps_max_verdict_without_setting() -> None:
    merged = merge_verdicts(
        {"verdict": "none", "system": [], "reason": "first pass"},
        {"verdict": "full", "system": [1], "reason": "reversed pass"},
    )

    assert merged["verdict"] == "full"
    assert merged["system"] == [1]


def test_comparison_overview_exports_only_dashboard_fields() -> None:
    stage = {
        "truth_items": 2,
        "matched": 1,
        "recall": 0.5,
        "alignment": 1.0,
        "f1": 0.667,
        "volume_ratio": 0.5,
        "not_covered": [],
    }
    report = {
        "workers": {"competitor_intelligence": stage},
        "verification": stage,
        "cross_signal": stage,
        "decision": {"options": stage},
    }

    overview = coverage_overview(report)

    assert overview == {
        "mean_recall": 0.5,
        "mean_alignment": 1.0,
        "mean_f1": 0.667,
        "pooled_recall": 0.5,
        "stages_with_zero_recall": [],
    }


def test_aggregate_comparison_report_keeps_only_dashboard_metrics(tmp_path: Path) -> None:
    files = []
    for index, recall in enumerate((0.4, 0.6), start=1):
        path = tmp_path / f"comparison-{index}.json"
        path.write_text(
            json.dumps(
                {
                    "ground_truth": f"dataset-{index}",
                    "run_bundle": f"run-{index}",
                    "method": "llm judge",
                    "query": {"same_company": True},
                    "overview": {
                        "mean_recall": recall,
                        "mean_alignment": 0.8,
                        "mean_f1": 0.6,
                        "pooled_recall": 0.5,
                        "unrendered": 0.1,
                    },
                }
            ),
            encoding="utf-8",
        )
        files.append(path)

    report = build_report(files)

    assert report["metrics"] == {
        "mean_recall": 0.5,
        "mean_alignment": 0.8,
        "mean_f1": 0.6,
        "pooled_recall": 0.5,
    }
    assert set(report["comparisons"][0]) == {"ground_truth", "run_bundle", "method", "metrics"}
    assert set(report["comparisons"][0]["metrics"]) == {
        "mean_recall", "mean_alignment", "mean_f1", "pooled_recall"
    }
