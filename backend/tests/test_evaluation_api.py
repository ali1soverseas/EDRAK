from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edrak.api.deps import get_current_session
from edrak.api.routers import evaluation


def _client(tmp_path, monkeypatch) -> TestClient:
    reports = tmp_path / "reports"
    agent = reports / "run1" / "decision"
    agent.mkdir(parents=True)
    (reports / "run1" / "pipeline_report.json").write_text(
        '{"total":2,"passed":1,"failed":1,"warnings":0,'
        '"checks":[{"check":"recommendation trace","passed":false,"detail":"signal missing","severity":"error"}]}',
        encoding="utf-8",
    )
    (agent / "decision_report.json").write_text(
        '{"agent":"decision","model":"m","thresholds":{"faithfulness":0.7},"files":[{"file":"f",'
        '"metrics":{"units":4,"units_flagged":1,"faithfulness_avg":0.8},'
        '"checks":[{"check":"a","passed":true},{"check":"b","passed":false,"detail":"bad","severity":"error"},'
        '{"check":"c","passed":false,"detail":"missing optional trace metadata","severity":"warn"}],'
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
    assert run["pipeline_report"]["checks"][0]["check"] == "recommendation trace"
    assert run["pipeline_report"]["failed"] == 1
    agent = run["agents"][0]
    assert (agent["checks"], agent["checks_passed"], agent["units_flagged"]) == (2, 1, 1)
    assert "judged_avg" not in agent
    assert "passed_checks" not in agent
    assert "domain_metrics" not in agent
    assert "metrics_used" not in agent
    assert agent["failed_checks"][0]["check"] == "b"
    assert agent["failed_checks"][0]["passed"] is False
    assert agent["file_details"][0]["file"] == "f"
    assert [check["check"] for check in agent["file_details"][0]["checks"]] == ["a", "b"]
    assert agent["file_details"][0]["flagged_units"][0]["flags"] == ["unfaithful"]
    assert client.get("/api/evaluation/runs/run1/agents/metrics").status_code == 404


def test_agent_summary_omits_unrendered_domain_metrics() -> None:
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
    assert "judged_avg" not in rag
    assert "domain_metrics" not in rag
    assert "metrics_used" not in rag

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
    assert "domain_metrics" not in orchestration


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
                        "result": [{"excerpt": "retrieved result", "api_token": "must be redacted"}],
                        "latency_ms": 25.0,
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
    assert set(quality) == {"dimensions"}
    assert "dimensions" not in summary["file_details"][0]["execution_quality"]
    assert summary["file_details"][0]["execution_quality"]["errors"] == 1
    assert quality["dimensions"]["tool_selection"] == {
        "assessable_files": 1, "partial_files": 0, "pending_judge_files": 0,
        "files": 2, "score": 0.9, "reasons": ["trace missing"],
    }
    assert quality["dimensions"]["tool_arguments"]["partial_files"] == 1
    assert quality["dimensions"]["tool_result_use"]["reasons"] == ["payload disabled"]
    assert summary["file_details"][0]["execution_quality"]["call_details"] == [{
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
        "result": [{"excerpt": "retrieved result", "api_token": "[REDACTED]"}],
        "latency_ms": 25.0,
    }]
    assert summary["file_details"][0]["execution_quality"]["call_details"][0]["result"] == [
        {"excerpt": "retrieved result", "api_token": "[REDACTED]"}
    ]


def test_run_summary_does_not_read_legacy_telemetry(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    report_dir = tmp_path / "reports" / "run1" / "customer_trends"
    report_dir.mkdir()
    (report_dir / "customer_trends_report.json").write_text(
        '{"agent":"customer_trends","files":[{"file":"worker.json","execution_quality":'
        '{"metrics":{"calls":1,"call_details":[{"tool":"social_search",'
        '"worker":"customer_trends","task_id":"task-1","status":"ok",'
        '"argument_names":["query","platform"]}]}}}]}',
        encoding="utf-8",
    )
    telemetry_dir = tmp_path / "runs" / "run1" / "inputs"
    (telemetry_dir / "telemetry.json").write_text(
        '{"spans":['
        '{"span_id":"worker-span","attributes":{"edrak.agent.type":"customer_trends",'
        '"edrak.task.id":"task-1"}},'
        '{"span_id":"tool-span","parent_span_id":"worker-span","attributes":'
        '{"edrak.tool.name":"social_search","edrak.tool.status":"ok",'
        '"edrak.tool.argument.values":"{\\"query\\":\\"NVIDIA AI rack\\",'
        '\\"platform\\":\\"reddit\\",\\"api_key\\":\\"secret\\"}"}}]}',
        encoding="utf-8",
    )

    response = client.get("/api/evaluation/runs/run1").json()
    customer_agent = next(agent for agent in response["agents"] if agent["agent"] == "customer_trends")
    call = customer_agent["file_details"][0]["execution_quality"]["call_details"][0]
    assert "argument_values" not in call


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
