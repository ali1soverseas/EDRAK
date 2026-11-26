import importlib.util
import json
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "Evaluation"
    / "version_comparison"
    / "compare.py"
)
_SPEC = importlib.util.spec_from_file_location("version_comparison", _SCRIPT)
compare_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compare_module)


def _report(run_id, score):
    metrics = {
        "correctness": score,
        "faithfulness": score,
        "completeness": score,
    }
    return {
        "run_id": run_id,
        "ground_truth": "same-dataset",
        "evaluation_mode": "combined",
        "workers": [
            {
                "worker": "market_intelligence",
                "status": "completed",
                "finding_count": 1,
                "evaluation": {
                    "worker_result_quality": {"metrics": metrics},
                    "coverage_score": score,
                },
            }
        ],
    }


def test_higher_overall_score_wins_even_within_metric_tie_margin(tmp_path):
    report_a = tmp_path / "a.json"
    report_b = tmp_path / "b.json"
    report_a.write_text(json.dumps(_report("run-a", 0.582)), encoding="utf-8")
    report_b.write_text(json.dumps(_report("run-b", 0.584)), encoding="utf-8")

    result = compare_module.compare(report_a, report_b, "Optimized", "GraphRAG")

    assert abs(result["overall_b"] - result["overall_a"]) < compare_module.TIE_MARGIN
    assert result["verdict"] == "GraphRAG"
