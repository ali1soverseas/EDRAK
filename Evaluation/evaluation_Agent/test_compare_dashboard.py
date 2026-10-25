from __future__ import annotations

import json
from pathlib import Path

from Evaluation.evaluation_Agent.build_overall_comparison_report import build_report
from Evaluation.evaluation_Agent.compare import (
    Judge,
    coverage_overview,
    merge_verdicts,
    score,
    semantic_candidates_for,
)


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

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_semantic_candidates_rank_by_embedding_similarity() -> None:
    candidates = semantic_candidates_for(
        [1.0, 0.0],
        [
            [0.1, 0.99],
            [0.95, 0.1],
            [0.0, 1.0],
        ],
        2,
    )

    assert candidates == [1, 0]


def test_score_uses_semantic_shortlist_for_rescue() -> None:
    class RecordingJudge:
        vectors = {
            "expected idea": [1.0, 0.0],
            "candidate 0": [0.2, 0.98],
            "candidate 1": [1.0, 0.0],
            "candidate 2": [0.8, 0.6],
            "candidate 3": [0.6, 0.8],
            "candidate 4": [0.9, 0.1],
            "candidate 5": [-1.0, 0.0],
        }
        rescued_candidates = None

        def match(self, label, truth, system):
            return [{"verdict": "none", "system": [], "reason": "unmatched"}]

        def embed(self, texts):
            return [self.vectors[text] for text in texts]

        def rescue(self, label, truth, system, candidates):
            self.rescued_candidates = candidates
            return {}

    judge = RecordingJudge()
    score(
        {"judge": judge, "consistency": False},
        "findings",
        [{"statement": "expected idea"}],
        [{"statement": f"candidate {index}"} for index in range(6)],
        ("statement",),
    )

    assert judge.rescued_candidates == {0: [1, 4, 2, 3, 0]}


def test_judge_embeddings_preserve_order_and_blank_positions() -> None:
    class Embeddings:
        @staticmethod
        def create(model, input):
            assert model == "text-embedding-3-small"
            assert input == ["first", "last"]
            return type(
                "Response",
                (),
                {
                    "data": [
                        type("Embedding", (), {"index": 1, "embedding": [0.0, 1.0]})(),
                        type("Embedding", (), {"index": 0, "embedding": [1.0, 0.0]})(),
                    ]
                },
            )()

    judge = object.__new__(Judge)
    judge.client = type("Client", (), {"embeddings": Embeddings()})()

    assert judge.embed(["first", " ", "last"]) == [
        [1.0, 0.0],
        [0.0, 0.0],
        [0.0, 1.0],
    ]

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
