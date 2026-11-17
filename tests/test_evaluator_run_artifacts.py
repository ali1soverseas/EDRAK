import json
from pathlib import Path

import pytest

from Evaluation.generic import evaluator

WORKERS = (
    "competitor_intelligence",
    "customer_trends",
    "internal_intelligence",
    "market_intelligence",
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _rubric(agent: str) -> dict:
    requirement_id = f"{agent}-requirement"
    return {
        "agent": agent,
        "dataset_version": "test",
        "research_requirements": [{"id": requirement_id, "requirement": "Evaluate evidence quality."}],
        "test_cases": [{
            "test_case": {"id": f"{agent}-case", "input_requirements": [requirement_id]},
            "golden_reference": {
                "expected_findings": [{
                    "id": f"{agent}-finding",
                    "requirement_id": requirement_id,
                    "finding": "Evidence quality",
                    "primary_claim": "Evidence quality is assessed.",
                    "acceptance_criteria": ["The report assesses evidence quality."],
                    "acceptable_alternatives": [],
                    "priority": "mandatory",
                }]
            },
        }],
    }


def _write_combined_golden(path: Path) -> None:
    _write_json(path, {
        "dataset_name": "combined-fixture",
        "dataset_version": "test",
        "worker_datasets": {worker: _rubric(worker) for worker in WORKERS},
        "verification_stage_rubric": _rubric("verification_stage"),
        "cross_signal": _rubric("cross_signal"),
        "decision_analysis": _rubric("decision_analysis"),
    })


def _make_run_artifacts(run_dir: Path) -> None:
    worker_outputs = []
    for worker in WORKERS:
        finding_id = f"{worker}-finding"
        evidence_id = f"{worker}-evidence"
        task_dir = run_dir / worker / f"{worker}-task.a1"
        task_input = {
            "task_id": f"{worker}-task",
            "worker": worker,
            "goal": f"Research {worker}.",
            "requirements": [],
        }
        task_output = {
            "task_id": f"{worker}-task",
            "worker": worker,
            "status": "completed",
            "findings": [{
                "finding_id": finding_id,
                "statement": f"Finding from {worker}.",
                "evidence_refs": [{"evidence_id": evidence_id}],
            }],
            "evidence": [{
                "evidence_id": evidence_id,
                "source_title": f"Source for {worker}",
                "excerpt": f"Evidence text for {worker}.",
            }],
            "gaps": [],
            "conflicts": [],
        }
        _write_json(task_dir / "input.json", task_input)
        _write_json(task_dir / "output.json", task_output)
        worker_outputs.append(task_output)

    verified_findings = [
        {
            "finding_id": item["findings"][0]["finding_id"],
            "worker": item["worker"],
            "statement": item["findings"][0]["statement"],
            "verification_status": "verified",
        }
        for item in worker_outputs
    ]
    _write_json(
        run_dir / "stages" / "verification" / "input.json",
        {"request": {"request_id": run_dir.name}, "agent_outputs": worker_outputs},
    )
    _write_json(
        run_dir / "stages" / "verification" / "output.json",
        {
            "status": "completed",
            "verification_result": {
                "decision": {"status": "verified"},
                "findings": [
                    {
                        **finding,
                        "evidence_ids": [
                            item["evidence"][0]["evidence_id"]
                            for item in worker_outputs
                            if item["worker"] == finding["worker"]
                        ],
                    }
                    for finding in verified_findings
                ],
            },
        },
    )

    signal_id = "signal-1"
    supports = [
        {"finding_id": finding["finding_id"], "relevance": "supports the signal"}
        for finding in verified_findings[:2]
    ]
    cross_signal = {
        "status": "completed",
        "signals": [{
            "signal_id": signal_id,
            "title": "Two-worker signal",
            "supporting_findings": supports,
        }],
    }
    _write_json(
        run_dir / "stages" / "cross_signal" / "input.json",
        {"verified_findings": verified_findings},
    )
    _write_json(
        run_dir / "stages" / "cross_signal" / "output.json",
        {"status": "completed", "cross_signal": cross_signal},
    )
    _write_json(
        run_dir / "stages" / "decision_analysis" / "input.json",
        {
            "business_request": {"goal": "Choose a response."},
            "cross_signal": cross_signal,
        },
    )
    _write_json(
        run_dir / "stages" / "decision_analysis" / "output.json",
        {
            "status": "completed",
            "decision_analysis": {
                "question_recommendations": [],
                "options": [{
                    "key": "differentiate",
                    "title": "Differentiate",
                    "supporting_signal_ids": [signal_id],
                }],
            },
        },
    )


def test_cli_evaluates_run_against_combined_golden_with_mandatory_judge_and_second_pass(
    tmp_path, monkeypatch
):
    run_dir = tmp_path / "run-123"
    output_dir = tmp_path / "evaluation"
    golden_path = tmp_path / "combined-golden.json"
    _make_run_artifacts(run_dir)
    _write_combined_golden(golden_path)
    evaluated = []

    def fake_evaluate(report, ground_truth, evidence, **kwargs):
        evaluated.append({
            "agent": ground_truth["agent"],
            "use_judge": kwargs["use_judge"],
            "second_pass": kwargs["second_pass"],
        })
        return {
            "worker": report.get("worker"),
            "finding_count": len(evaluator.expected_findings(ground_truth)),
            "coverage_score": 1.0,
            "judge_status": "completed",
            "judge_errors": [],
            "second_pass_summary": {"enabled": True, "status": "not_needed"},
        }

    monkeypatch.setattr(evaluator, "evaluate", fake_evaluate)

    exit_code = evaluator.main([
        "--run-dir", str(run_dir),
        "--ground-truth", str(golden_path),
        "--out-dir", str(output_dir),
    ])

    report_path = output_dir / "run_evaluation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["ground_truth"] == "combined-fixture"
    assert report["judge_enabled"] is True
    assert report["worker_task_count"] == 4
    assert set(report["workers_evaluated"]) == set(WORKERS)
    assert report["stage_count"] == 3
    assert len(evaluated) == 7
    assert all(item["use_judge"] and item["second_pass"] for item in evaluated)
    assert report["stages"]["verification"]["deterministic_checks"]["assessment_coverage"] == 1.0
    assert report["stages"]["cross_signal"]["deterministic_checks"]["signals_without_two_distinct_supporting_findings"] == []
    assert report["stages"]["decision_analysis"]["deterministic_checks"]["items_with_unknown_signal_references"] == {}
    assert all(
        (output_dir / "stages" / f"{stage}_evaluation.json").is_file()
        for stage in ("verification", "cross_signal", "decision_analysis")
    )


def test_evaluate_rejects_optional_judge_or_second_pass():
    with pytest.raises(ValueError, match="LLM judging is mandatory"):
        evaluator.evaluate({}, {}, [], use_judge=False)
    with pytest.raises(ValueError, match="Second-pass review is mandatory"):
        evaluator.evaluate({}, {}, [], second_pass=False)


def test_openai_judge_loads_key_from_repository_env(monkeypatch):
    expected_env_path = Path(evaluator.__file__).resolve().parents[2] / ".env"

    def fake_load_dotenv(path, override):
        assert Path(path) == expected_env_path
        assert override is False
        monkeypatch.setenv("OPENAI_API_KEY", "test-token")

    monkeypatch.setattr(evaluator, "load_dotenv", fake_load_dotenv)
    monkeypatch.setenv("OPENAI_MODEL", "test/model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("OPENAI_TIMEOUT_S", "120")
    constructed = {}

    class FakeJudge:
        def __init__(self, **kwargs):
            constructed.update(kwargs)

    monkeypatch.setattr(evaluator, "OpenAIDeepEvalModel", FakeJudge)

    evaluator._make_openai_judge({"judge": {"provider": "openai"}})

    assert constructed == {
        "model_name": "test/model",
        "base_url": "https://api.example/v1",
        "api_key": "test-token",
        "timeout": 120.0,
    }


def test_openai_judge_requires_api_key(monkeypatch):
    monkeypatch.setattr(evaluator, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not configured"):
        evaluator._make_openai_judge({"judge": {"provider": "openai"}})


def test_openai_judge_rejects_non_openai_provider(monkeypatch):
    monkeypatch.setattr(evaluator, "load_dotenv", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="supported provider: 'openai'"):
        evaluator._make_openai_judge({"judge": {"provider": "nvidia"}})


def test_run_artifact_evaluation_resumes_from_market_intelligence(tmp_path, monkeypatch):
    run_dir = tmp_path / "run-resume"
    output_dir = tmp_path / "evaluation"
    golden_path = tmp_path / "combined-golden.json"
    _make_run_artifacts(run_dir)
    _write_combined_golden(golden_path)
    ground_truth = json.loads(golden_path.read_text(encoding="utf-8"))
    completed_workers = []
    for worker in WORKERS[:3]:
        task_dir = run_dir / worker / f"{worker}-task.a1"
        completed_workers.append({
            "worker": worker,
            "task_id": f"{worker}-task",
            "source_output": str(task_dir / "output.json"),
            "evaluation": {
                "judge_status": "completed",
                "second_pass_summary": {"enabled": True, "status": "completed"},
            },
            "mandatory_evaluation_failures": [],
        })
    _write_json(output_dir / "run_evaluation_report.json", {
        "run_id": run_dir.name,
        "ground_truth": "combined-fixture",
        "workers": completed_workers,
        "stages": {},
    })
    evaluated = []

    def fake_evaluate(report, ground_truth, evidence, **kwargs):
        evaluated.append(report["worker"])
        return {
            "worker": report["worker"],
            "finding_count": len(evaluator.expected_findings(ground_truth)),
            "coverage_score": 1.0,
            "judge_status": "completed",
            "judge_errors": [],
            "second_pass_summary": {"enabled": True, "status": "not_needed"},
        }

    monkeypatch.setattr(evaluator, "evaluate", fake_evaluate)
    summary = evaluator.evaluate_run_artifacts(
        run_dir,
        output_dir,
        ground_truth,
        resume_from="market_intelligence",
    )

    assert evaluated == [
        "market_intelligence",
        "verification",
        "cross_signal",
        "decision_analysis",
    ]
    assert summary["resumed_from"] == "market_intelligence"
    assert summary["resumed_workers"] == list(WORKERS[:3])
    assert summary["worker_task_count"] == 4
    assert summary["stage_count"] == 3
    assert summary["errors"] == []
