from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edrak.api.deps import get_current_session
from edrak.api.routers import evaluation


def _client(tmp_path, monkeypatch) -> TestClient:
    reports = tmp_path / "reports"
    agent = reports / "run1" / "decision"
    agent.mkdir(parents=True)
    (agent / "decision_report.json").write_text(
        '{"agent":"decision","model":"m","thresholds":{"faithfulness":0.7},"files":[{"file":"f",'
        '"metrics":{"units":4,"units_flagged":1,"faithfulness_avg":0.8},'
        '"checks":[{"check":"a","passed":true},{"check":"b","passed":false,"detail":"bad","severity":"error"}],'
        '"flagged_units":[{"unit_id":"u1","statement":"example","flags":["unfaithful"]}]}]}',
        encoding="utf-8",
    )
    metrics = reports / "run1" / "metrics"
    metrics.mkdir()
    (metrics / "metrics_report.json").write_text(
        '{"agent":"metrics","files":[{"checks":[],"metrics":{}}]}', encoding="utf-8")
    (reports / "compare").mkdir()
    (reports / "compare" / "cmp1.json").write_text(
        '{"ground_truth":"gt","run_bundle":"run1","method":"x","overview":{"mean_recall":0.5}}', encoding="utf-8")
    monkeypatch.setenv("EDRAK_EVAL_REPORTS_DIR", str(reports))
    run_inputs = tmp_path / "runs" / "run1" / "inputs"
    run_inputs.mkdir(parents=True)
    (run_inputs / "initial_input.json").write_text(
        '{"goal":"Assess the market","business_context":{"use_case":"market_entry"}}', encoding="utf-8")
    monkeypatch.setenv("EDRAK_EVAL_RUNS_DIR", str(tmp_path / "runs"))
    app = FastAPI()
    app.include_router(evaluation.router, prefix="/api")
    app.dependency_overrides[get_current_session] = lambda: None
    return TestClient(app)


def test_lists_and_summarises_runs(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/evaluation/runs").json()["runs"][0]["agents"] == ["decision"]
    run = client.get("/api/evaluation/runs/run1").json()
    assert run["goal"] == "Assess the market"
    assert run["use_case"] == "market_entry"
    agent = run["agents"][0]
    assert (agent["checks"], agent["checks_passed"], agent["units_flagged"]) == (2, 1, 1)
    assert agent["judged_avg"] == {"faithfulness": 0.8}
    assert agent["passed_checks"][0]["check"] == "a"
    assert agent["failed_checks"][0]["check"] == "b"
    assert agent["failed_checks"][0]["passed"] is False
    assert agent["file_details"][0]["file"] == "f"
    assert agent["file_details"][0]["flagged_units"][0]["flags"] == ["unfaithful"]
    assert client.get("/api/evaluation/runs/run1/agents/metrics").status_code == 404


def test_summarises_rag_and_orchestration_metrics() -> None:
    rag = evaluation._agent_summary({
        "agent": "rag",
        "files": [
            {"metrics_used": ["context_relevancy", "rag_retrieval_quality"],
             "metrics": {"context_relevancy_score": 0.9, "mean_retrieval_score": 0.6,
                         "queries": 1, "retrieved_chunks": 2, "empty_chunks": 0,
                         "duplicate_chunk_pairs": 0, "context_precision_error": "reference missing",
                         "context_recall_error": "reference missing", "faithfulness_score": 0.8,
                         "answer_relevancy_score": 0.7, "retrieval_latency_ms": 120,
                         "retrieval_latency_calls": 2,
                         "answer_source": "matched_worker_findings"}},
            {"metrics": {"context_relevancy_score": 0.3, "mean_retrieval_score": 0.4,
                         "queries": 1, "retrieved_chunks": 1, "empty_chunks": 1,
                         "duplicate_chunk_pairs": 1,
                         "faithfulness_score": 0.6, "answer_relevancy_score": 0.9,
                         "retrieval_latency_ms": 80, "retrieval_latency_calls": 1}},
        ],
    })
    assert rag["judged_avg"]["context_relevancy"] == 0.6
    assert rag["domain_metrics"] == {
        "mean_retrieval_score": 0.533,
        "context_precision": {"score": None, "reason": "reference missing"},
        "context_recall": {"score": None, "reason": "reference missing"},
        "context_relevancy": {"score": 0.6, "reason": None},
        "answer_source": "matched_worker_findings",
        "queries": 2,
        "retrieved_chunks": 3,
        "empty_chunks": 1,
        "duplicate_chunk_pairs": 1,
        "retrieval_latency_ms": 106.667,
    }
    assert "faithfulness" not in rag["domain_metrics"]
    assert "answer_relevancy" not in rag["domain_metrics"]
    assert "faithfulness" not in rag["judged_avg"]
    assert "relevancy" not in rag["judged_avg"]

    orchestration = evaluation._agent_summary({
        "agent": "orchestration",
        "files": [{"metrics": {"task_completion_pct": 75, "workflow_success_pct": 0,
                               "dependency_integrity_pct": 50, "failure_rate_pct": 25,
                               "retry_rate_pct": 20, "execution_duration_seconds": 42.5,
                               "planned_tasks": 4, "failed_tasks": 1, "dependency_edges": 2,
                               "dependency_violations": 1,
                               "missing_tasks": 0, "missing_dependencies": 0,
                               "execution_quality_status": "not_assessable"}}],
    })
    assert orchestration["domain_metrics"] == {
        "task_completion_pct": 75.0,
        "workflow_success_pct": 0.0,
        "dependency_integrity_pct": 50.0,
        "failure_rate_pct": 25.0,
        "retry_rate_pct": 20.0,
        "execution_duration_seconds": 42.5,
        "planned_tasks": 4,
        "failed_tasks": 1,
        "dependency_edges": 2,
        "dependency_violations": 1,
        "missing_tasks": 0,
        "missing_dependencies": 0,
        "execution_quality_status": "not_assessable",
    }


def test_summarises_execution_quality_dimensions() -> None:
    summary = evaluation._agent_summary({
        "agent": "internal_intelligence",
        "files": [
            {"file": "worker_trace.json", "execution_quality": {
                "status": "partial",
                "metrics": {
                    "calls": 3,
                    "errors": 1,
                    "repeated_calls": 2,
                    "call_details": [{
                        "tool": "internal_rag.retrieve",
                        "provider": "chroma",
                        "status": "ok",
                        "argument_names": ["query", "top_k"],
                        "argument_types": {"query": "str", "top_k": "int"},
                        "argument_values": {
                            "query": "search internal documentation",
                            "top_k": 2,
                            "api_token": "must be redacted",
                        },
                        "result_count": 2,
                        "result_summary": {"type": "array", "count": 2},
                        "latency_ms": 25.0,
                        "result": [{"excerpt": "must not be exposed"}],
                    }],
                },
                "dimensions": {
                    "tool_selection": {"status": "assessable", "score": 0.9, "reason": ""},
                    "tool_arguments": {"status": "partial", "score": None, "reason": "one schema missing"},
                    "tool_result_use": {"status": "not_assessable", "score": None, "reason": "payload disabled"},
                },
            }},
            {"execution_quality": {
                "status": "not_assessable",
                "metrics": {"calls": 0},
                "dimensions": {
                    "tool_selection": {"status": "not_assessable", "score": None, "reason": "trace missing"},
                },
            }},
        ],
    })

    quality = summary["execution_quality"]
    assert quality["status"] == "partial"
    assert quality["files_with_trace"] == 1
    assert quality["calls"] == 3
    assert quality["errors"] == 1
    assert quality["repeated_calls"] == 2
    assert summary["file_details"][0]["execution_quality"]["errors"] == 1
    assert quality["dimensions"]["tool_selection"] == {
        "assessable_files": 1, "partial_files": 0, "pending_judge_files": 0,
        "files": 2, "score": 0.9, "reasons": ["trace missing"],
    }
    assert quality["dimensions"]["tool_arguments"]["partial_files"] == 1
    assert quality["dimensions"]["tool_result_use"]["reasons"] == ["payload disabled"]
    assert quality["call_details"] == [{
        "file": "worker_trace.json",
        "tool": "internal_rag.retrieve",
        "provider": "chroma",
        "status": "ok",
        "argument_names": ["query", "top_k"],
        "argument_types": {"query": "str", "top_k": "int"},
        "argument_values": {
            "query": "search internal documentation",
            "top_k": 2,
            "api_token": "[REDACTED]",
        },
        "result_count": 2,
        "result_summary": {"type": "array", "count": 2},
        "latency_ms": 25.0,
    }]
    assert summary["file_details"][0]["execution_quality"]["call_details"] == quality["call_details"]
    assert "result" not in summary["file_details"][0]["execution_quality"]["call_details"][0]


def test_serves_comparisons_and_rejects_bad_ids(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/evaluation/comparisons").json()["comparisons"][0]["id"] == "cmp1"
    assert client.get("/api/evaluation/comparisons/cmp1").json()["ground_truth"] == "gt"
    assert client.get("/api/evaluation/runs/..%2Fsecret").status_code == 404
    assert client.get("/api/evaluation/comparisons/missing").status_code == 404


def test_requires_sign_in() -> None:
    app = FastAPI()
    app.include_router(evaluation.router, prefix="/api")
    assert TestClient(app).get("/api/evaluation/runs").status_code in (401, 500)
