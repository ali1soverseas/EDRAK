from __future__ import annotations

import json
import sys
import threading
from argparse import Namespace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import Evaluation.evaluation_Agent.edrak_eval_agent_enhanced as evaluator_module
from Evaluation.evaluation_Agent.edrak_eval_agent_enhanced import (
    Cfg,
    Ctx,
    EvalAgent,
    Judge,
    detect,
    _execution_calls,
    _golden_answer_for_rag,
    dashboard_metrics,
    eval_cross_signal,
    eval_decision,
    eval_execution_trace,
    eval_metrics,
    eval_orchestration,
    eval_rag,
    eval_telemetry,
    eval_verification,
    eval_worker,
    pipeline_checks,
)
from Evaluation.evaluation_Agent.edrak_eval_graph_enhanced import main as graph_main


class StubJudge:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[tuple[str, str, str, list[str], float, tuple | None]] = []
        self.successes = 0
        self.errors = 0
        self.skipped = 0
        self.rate_limit_open = False
        self.total_latency_seconds = 0.0
        self.metric_calls: dict[str, int] = {}
        self.metric_failures: dict[str, int] = {}
        self.metric_skips: dict[str, int] = {}

    def run(
        self,
        metric: str,
        question: str,
        output: str,
        context: list[str],
        threshold: float,
        spec: tuple | None = None,
        expected_output: str | None = None,
    ) -> dict[str, object]:
        self.calls.append((metric, question, output, context, threshold, spec))
        self.metric_calls[metric] = self.metric_calls.get(metric, 0) + 1
        if not self.enabled:
            self.skipped += 1
            self.metric_skips[metric] = self.metric_skips.get(metric, 0) + 1
            return {"score": None, "reason": "", "error": "dry-run"}
        self.successes += 1
        return {"score": 0.9, "reason": "stubbed", "error": ""}

def make_cfg(extra_metrics: bool = True) -> Cfg:
    return Cfg(
        Namespace(
            deep=False,
            context_relevancy=False,
            extra_metrics=extra_metrics,
            include_tool_payloads=False,
            faithfulness_threshold=0.7,
            relevancy_threshold=0.5,
            context_relevancy_threshold=0.5,
            specificity_threshold=0.5,
            insight_threshold=0.5,
            actionability_threshold=0.5,
        )
    )


def test_worker_extra_llm_metrics_are_opt_in() -> None:
    default_cfg = make_cfg(extra_metrics=False).for_agent("worker", worker_type="competitor_intelligence")
    expanded = make_cfg(extra_metrics=True)

    assert default_cfg.metrics == ["faithfulness", "relevancy"]
    assert expanded.for_agent("worker", worker_type="competitor_intelligence").metrics == [
        "faithfulness", "relevancy", "specificity", "actionability"]
    assert expanded.for_agent("worker", worker_type="internal_intelligence").metrics == [
        "faithfulness", "relevancy", "actionability"]
    assert expanded.for_agent("worker", worker_type="market_intelligence").metrics == [
        "faithfulness", "relevancy", "specificity", "insight"]
    assert expanded.for_agent("worker", worker_type="customer_trends").metrics == [
        "faithfulness", "relevancy", "insight"]


def test_dashboard_metric_export_omits_metrics_not_rendered() -> None:
    metrics = {
        "status": "completed",
        "units": 5,
        "units_flagged": 1,
        "faithfulness_tested": 2,
        "faithfulness_avg": 0.9,
        "faithfulness_pass_pct": 100,
        "context_relevancy_score": 0.9,
        "context_relevancy_error": "",
        "evidence_utilization_pct": 80,
        "mean_retrieval_score": 0.8,
        "avg_confidence": 0.7,
        "tool_names": ["search"],
        "signal_types": "convergence=2, dependency=1",
        "planned_tasks": 4,
        "result_tasks": 4,
        "successful_tasks": 3,
        "task_completion_pct": 100,
        "workflow_success_pct": 75,
        "dependency_integrity_pct": 100,
        "queries": 2,
        "retrieved_chunks": 8,
        "execution_duration_seconds": 12,
        "attempt": 2,
        "judge_errors": 1,
        "context_relevancy_reason": "judge unavailable",
    }

    exported = dashboard_metrics(metrics)

    assert set(exported) == {
        "status",
        "units",
        "units_flagged",
        "faithfulness_tested",
        "faithfulness_avg",
        "faithfulness_pass_pct",
        "context_relevancy_score",
        "context_relevancy_error",
        "tool_names",
        "signal_types",
        "planned_tasks",
        "result_tasks",
        "successful_tasks",
        "task_completion_pct",
        "workflow_success_pct",
        "judge_errors",
    }


def failed_check(result: dict, name: str) -> dict:
    return next(check for check in result["checks"] if check["check"] == name)


def test_judge_retries_using_rate_limit_header_and_unwraps_error(tmp_path: Path, monkeypatch) -> None:
    class RateLimitError(Exception):
        headers = {"retry-after": "3"}

    class Attempt:
        def exception(self) -> Exception:
            return RateLimitError("Please retry later")

    class RetryError(Exception):
        last_attempt = Attempt()

    judge = Judge("test-model", enabled=True, retries=2)
    waits: list[float] = []
    calls = 0

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def measure(*args) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryError("retry wrapper")
        return {"score": 0.9, "reason": "ok", "error": ""}

    monkeypatch.setattr("Evaluation.evaluation_Agent.edrak_eval_agent_enhanced.time.sleep", fake_sleep)
    monkeypatch.setattr(judge, "_measure", measure)

    result = judge.run("faithfulness", "question", "answer", ["evidence"], 0.7)

    assert result["score"] == 0.9
    assert calls == 2
    assert len(waits) == 1 and abs(waits[0] - 3.0) < 0.01
    assert judge.errors == 0


def test_judge_reports_underlying_error_after_retries(tmp_path: Path, monkeypatch) -> None:
    class RateLimitError(Exception):
        pass

    class Attempt:
        def exception(self) -> Exception:
            return RateLimitError("quota exceeded")

    class RetryError(Exception):
        last_attempt = Attempt()

    judge = Judge("test-model", enabled=True, retries=2)
    waits: list[float] = []
    calls = 0

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    def fail(*args) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise RetryError("retry wrapper")

    monkeypatch.setattr("Evaluation.evaluation_Agent.edrak_eval_agent_enhanced.time.sleep", fake_sleep)
    monkeypatch.setattr(judge, "_measure", fail)

    result = judge.run("faithfulness", "question", "answer", ["evidence"], 0.7)

    assert calls == 2
    assert len(waits) == 1 and abs(waits[0] - 2.0) < 0.01
    assert result["error"] == "RateLimitError: quota exceeded"
    assert judge.errors == 1


def test_judge_stops_new_requests_after_rate_limit_retries(tmp_path: Path, monkeypatch) -> None:
    class RateLimitError(Exception):
        headers = {"retry-after": "0"}

    judge = Judge("test-model", enabled=True, retries=2)
    calls = 0

    def fail(*args):
        nonlocal calls
        calls += 1
        raise RateLimitError("quota exceeded")

    monkeypatch.setattr("Evaluation.evaluation_Agent.edrak_eval_agent_enhanced.time.sleep", lambda _: None)
    monkeypatch.setattr(judge, "_measure", fail)

    first = judge.run("faithfulness", "q1", "answer", ["evidence"], 0.7)
    second = judge.run("relevancy", "q2", "answer", ["evidence"], 0.5)

    assert first["error"] == "RateLimitError: quota exceeded"
    assert second["error"] == "skipped: judge rate-limit circuit is open"
    assert calls == judge.calls == 2
    assert judge.errors == 1 and judge.skipped == 1
    assert judge.metric_failures == {"faithfulness": 1}
    assert judge.metric_skips == {"relevancy": 1}


def test_judge_does_not_cache_successful_results(monkeypatch) -> None:
    judge = Judge("test-model", enabled=True, retries=1)
    calls = 0

    def measure(*args) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"score": 0.9, "reason": "fresh score", "error": ""}

    monkeypatch.setattr(judge, "_measure", measure)

    first = judge.run("faithfulness", "q", "answer", ["evidence"], 0.7)
    second = judge.run("faithfulness", "q", "answer", ["evidence"], 0.7)

    assert first["reason"] == second["reason"] == "fresh score"
    assert calls == judge.calls == 2


def test_judge_calls_model_for_each_threshold(monkeypatch) -> None:
    judge = Judge("test-model", enabled=True, retries=1)
    measured_thresholds: list[float] = []

    def measure(metric, question, output, context, threshold, *args):
        measured_thresholds.append(threshold)
        return {"score": 0.9, "reason": "ok", "error": ""}

    monkeypatch.setattr(judge, "_measure", measure)

    judge.run("faithfulness", "q", "answer", ["evidence"], 0.7)
    judge.run("faithfulness", "q", "answer", ["evidence"], 0.8)

    assert measured_thresholds == [0.7, 0.8]
    assert judge.calls == 2


def test_judge_unit_runs_metrics_in_parallel_and_keeps_column_order(tmp_path: Path, monkeypatch) -> None:
    import threading
    from Evaluation.evaluation_Agent.edrak_eval_agent_enhanced import judge_unit

    judge = Judge("test-model", enabled=True, retries=1, workers=3)
    barrier = threading.Barrier(2, timeout=5)   # only passes if two metrics are in flight together

    def measure(metric, *args):
        if metric in ("faithfulness", "relevancy"):
            barrier.wait()
        return {"score": 0.9, "reason": metric, "error": ""}

    monkeypatch.setattr(judge, "_measure", measure)
    cols = judge_unit(judge, make_cfg(extra_metrics=False).for_agent("verification"), "q", "answer", ["evidence"])

    assert list(cols) == ["faithfulness_score", "faithfulness_pass", "faithfulness_reason", "faithfulness_note",
                          "relevancy_score", "relevancy_pass", "relevancy_reason", "relevancy_note"]
    assert cols["faithfulness_reason"] == "faithfulness" and cols["relevancy_reason"] == "relevancy"
    assert judge.calls == 2


def test_rag_evaluation_records_retrieval_quality_only() -> None:
    data = {
        "query": "What is the support policy?",
        "answer": "Support is available around the clock.",
        "retrieval_results": [
            {
                "chunk_id": "C1",
                "text": "Support is available around the clock.",
                "score": 0.8,
                "source_url": "https://docs.example.com/support",
            },
            {
                "chunk_id": "C2",
                "text": "Support is available around the clock.",
                "metadata": {"relevance_score": 1.2},
                "source_url": "https://help.example.com/support",
            },
        ],
    }
    judge = StubJudge(enabled=True)

    assert detect(data) == "rag"
    result = eval_rag(Path("rag.json"), data, Ctx(), judge, make_cfg())

    assert set(result["metrics"]) == {
        "context_precision_score", "context_recall_score", "context_relevancy_score",
        "context_precision_error", "context_recall_error", "context_relevancy_error",
    }
    assert result["metrics"]["context_relevancy_score"] == 0.9
    assert "answer_relevancy_score" not in result["metrics"]
    assert "faithfulness_score" not in result["metrics"]
    assert len(judge.calls) == 1
    assert failed_check(result, "retrieval scores are within [0, 1]")["passed"] is False
    assert failed_check(result, "retrieved chunks are not near-duplicates")["passed"] is False
    assert any(row["unit_type"] == "rag_answer" for row in result["rows"])


def test_rag_does_not_export_retrieval_latency_metrics() -> None:
    query = "Which products are available?"
    ctx = Ctx(telemetry_calls=[
        {
            "tool": "internal_rag.retrieve",
            "task_id": "task-1",
            "attempt": 1,
            "argument_values": {"query": query},
            "latency_ms": 25,
        },
        {
            "tool": "internal_rag.retrieve",
            "task_id": "task-1",
            "attempt": 1,
            "argument_values": {"query": "A different retrieval"},
            "latency_ms": 75,
        },
    ])
    result = eval_rag(
        Path("rag.json"),
        {"query": query, "task_id": "task-1", "attempt": 1, "retrieved_chunks": []},
        ctx,
        StubJudge(),
        make_cfg(),
    )

    assert not any("latency" in key for key in result["metrics"])


def test_rag_does_not_use_worker_findings_as_answer() -> None:
    rag_record = {
        "worker": "internal_intelligence",
        "task_id": "task-1",
        "attempt": 2,
        "query": "What is available?",
        "retrieved_chunks": [{"text": "The feature is available."}],
    }
    judge = StubJudge(enabled=True)
    result = eval_rag(
        Path("rag.json"),
        rag_record,
        Ctx(),
        judge,
        make_cfg(),
    )
    assert "answer_source" not in result["metrics"]
    assert "faithfulness_score" not in result["metrics"]
    assert "answer_relevancy_score" not in result["metrics"]
    assert "metrics_used" not in result
    assert not any(call[0] in {"relevancy", "faithfulness"} for call in judge.calls)
    assert not any(row["unit_type"] == "rag_answer" for row in result["rows"])


def test_rag_does_not_emit_answer_metrics_without_answer() -> None:
    result = eval_rag(
        Path("rag.json"),
        {"query": "Find evidence.", "retrieved_chunks": [{"text": "Evidence."}]},
        Ctx(),
        StubJudge(enabled=True),
        make_cfg(),
    )

    assert "faithfulness_error" not in result["metrics"]
    assert "answer_relevancy_error" not in result["metrics"]


def test_eval_agent_does_not_substitute_worker_findings_for_missing_rag_answer(tmp_path: Path) -> None:
    worker = tmp_path / "worker.json"
    worker.write_text(
        json.dumps({
            "worker": "internal_intelligence",
            "task_id": "task-1",
            "attempt": 1,
            "metadata": {"research_goal": "Assess product availability."},
            "evidence": [{
                "evidence_id": "ev-1",
                "text": "The feature is available.",
                "source_url": "https://example.com/product",
            }],
            "findings": [{
                "finding_id": "finding-1",
                "statement": "The feature is available.",
                "evidence_refs": [{"evidence_id": "ev-1", "relation": "supports"}],
            }],
        }),
        encoding="utf-8",
    )
    rag = tmp_path / "rag.json"
    rag.write_text(
        json.dumps({
            "worker": "internal_intelligence",
            "task_id": "task-1",
            "attempt": 1,
            "query": "What is available?",
            "retrieved_chunks": [{"text": "The feature is available."}],
        }),
        encoding="utf-8",
    )
    output = tmp_path / "reports"
    args = Namespace(
        inputs=[str(worker), str(rag)],
        out=str(output),
        agents=["rag"],
        task="",
        model="test-model",
        dry_run=False,
        deep=False,
        context_relevancy=False,
        no_extra_metrics=False,
        include_tool_payloads=False,
        faithfulness_threshold=0.7,
        relevancy_threshold=0.5,
        context_relevancy_threshold=0.5,
        specificity_threshold=0.5,
        insight_threshold=0.5,
        actionability_threshold=0.5,
    )
    agent = EvalAgent(args)
    judge = StubJudge(enabled=True)
    agent.judge = judge

    assert agent.load() is True
    assert agent.run() == 0

    rag_result = next(
        result for results in agent.per_agent.values() for result in results
        if result["file"] == rag.name
    )
    assert "answer_source" not in rag_result["metrics"]
    assert "faithfulness_score" not in rag_result["metrics"]
    assert "answer_relevancy_score" not in rag_result["metrics"]
    assert not any(call[0] in {"relevancy", "faithfulness"} for call in judge.calls)
    assert not any(row["unit_type"] == "rag_answer" for row in rag_result["rows"])


def test_golden_rag_reference_is_selected_by_use_case_and_worker(tmp_path: Path) -> None:
    dataset_inputs = tmp_path / "nvidia_product_launch_inputs" / "inputs"
    dataset_inputs.mkdir(parents=True)
    (dataset_inputs / "initial_input.json").write_text(
        json.dumps({"business_context": {"use_case": "product_launch"}}),
        encoding="utf-8",
    )
    (dataset_inputs / "worker_internal_intelligence.json").write_text(
        json.dumps({
            "worker": "internal_intelligence",
            "expected_output": "Curated reference answer.",
            "findings": [
                {"statement": "Reference finding one."},
                {"statement": "Reference finding two."},
            ],
        }),
        encoding="utf-8",
    )
    loaded = [
        (Path("initial_input.json"), "input", {"business_context": {"use_case": "product_launch"}}),
    ]

    reference = _golden_answer_for_rag(
        {"worker": "internal_intelligence", "query": "What is available?"},
        loaded,
        tmp_path,
    )

    assert reference == (
        "Curated reference answer.",
        "golden/ground_truth/nvidia_product_launch_inputs/inputs/worker_internal_intelligence.json",
    )


def test_query_specific_golden_rag_reference_skips_embedding_fallback(tmp_path: Path) -> None:
    dataset_inputs = tmp_path / "nvidia_product_launch_inputs" / "inputs"
    dataset_inputs.mkdir(parents=True)
    (dataset_inputs / "initial_input.json").write_text(
        json.dumps({"business_context": {"use_case": "product_launch"}}),
        encoding="utf-8",
    )
    (dataset_inputs / "rag_internal_intelligence.json").write_text(
        json.dumps({
            "worker": "internal_intelligence",
            "queries": [{
                "query": "What is available?",
                "expected_output": "Only the curated scenario assumptions are available.",
            }],
        }),
        encoding="utf-8",
    )

    reference = _golden_answer_for_rag(
        {"worker": "internal_intelligence", "query": "  WHAT is  available?  "},
        [
            (
                Path("initial_input.json"),
                "input",
                {"business_context": {"use_case": "product_launch"}},
            ),
        ],
        embed_texts=lambda _: (_ for _ in ()).throw(AssertionError("embedding fallback ran")),
        ground_truth_root=tmp_path,
    )

    assert reference == (
        "Only the curated scenario assumptions are available.",
        "golden/ground_truth/nvidia_product_launch_inputs/inputs/rag_internal_intelligence.json",
    )


def test_competitive_golden_reference_states_internal_data_is_unavailable() -> None:
    loaded = [
        (
            Path("initial_input.json"),
            "input",
            {"business_context": {"use_case": "competitive_intelligence"}},
        ),
    ]

    reference = _golden_answer_for_rag(
        {"worker": "internal_intelligence", "query": "Internal assessment of NVIDIA capabilities"},
        loaded,
    )

    assert reference is not None
    assert "synthetic planning references" in reference[0]
    assert "workload, configuration, test conditions, and source" in reference[0]
    assert reference[1] == (
        "golden/ground_truth/nvidia_competitive_intelligence_inputs/"
        "inputs/worker_internal_intelligence.json"
    )


def test_rag_uses_provided_reference_for_context_precision_and_recall() -> None:
    judge = StubJudge(enabled=True)
    result = eval_rag(
        Path("rag.json"),
        {
            "query": "What is available?",
            "expected_output": "The feature is available.",
            "expected_output_source": "golden/ground_truth/example/inputs/worker_internal_intelligence.json",
            "retrieved_chunks": [{"text": "The feature is available."}],
        },
        Ctx(),
        judge,
        make_cfg(),
    )

    assert result["metrics"]["context_precision_score"] == 0.9
    assert result["metrics"]["context_recall_score"] == 0.9
    assert {call[0] for call in judge.calls} >= {"context_precision", "context_recall"}


def test_tool_trace_scores_execution_dimensions_and_redacts_payloads() -> None:
    payload = {
        "execution_trace": {
            "complete": True,
            "allowed_tools": ["search", "fetch"],
            "expected_sequence": ["search", "fetch"],
            "calls": [
                {
                    "tool": "search",
                    "status": "ok",
                    "args": {"query": "pricing", "api_key": "secret-search-key"},
                    "argument_schema": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {"query": {"type": "string"}},
                    },
                    "result_summary": {"count": 1, "access_token": "secret-result-token"},
                },
                {
                    "tool": "fetch",
                    "status": "error",
                    "fallback_used": True,
                    "error_code": "timeout",
                    "args": {"url": "https://example.com/pricing"},
                    "argument_schema": {
                        "type": "object",
                        "required": ["url"],
                        "properties": {"url": {"type": "string"}},
                    },
                    "result_summary": "Used cached result.",
                },
            ],
        }
    }
    judge = StubJudge(enabled=True)

    result = eval_execution_trace(
        payload,
        "Compare pricing.",
        "The search result supports the comparison.",
        judge,
        include_payloads=True,
    )

    assert result["status"] == "assessable"
    assert result["metrics"]["calls"] == 2
    assert result["metrics"]["errors"] == 1
    assert result["metrics"]["sequence_missing"] == []
    assert result["metrics"]["schema_checks"] == 2
    assert result["metrics"]["schema_errors"] == []
    assert result["dimensions"]["tool_selection"]["status"] == "assessable"
    assert result["dimensions"]["tool_arguments"]["status"] == "assessable"
    assert result["dimensions"]["tool_result_use"]["status"] == "assessable"
    assert result["dimensions"]["workflow_sequence"]["status"] == "assessable"
    assert result["dimensions"]["error_handling"]["status"] == "assessable"
    assert result["dimensions"]["efficiency"]["status"] == "assessable"
    assert len(judge.calls) == 5
    assert all(call[5] is not None for call in judge.calls)
    assert all(
        secret not in str((call[2], call[3]))
        for call in judge.calls
        for secret in ("secret-search-key", "secret-result-token")
    )
    assert failed_check(result, "tool calls stay within declared/available tools")["passed"] is True
    assert failed_check(result, "tool arguments satisfy supplied schemas")["passed"] is True
    assert failed_check(result, "tool failures are handled or explicitly surfaced")["passed"] is True


def test_span_tool_results_are_preserved_and_only_payloads_are_judged_opt_in() -> None:
    data = {
        "worker": "internal_intelligence",
        "spans": [{
            "name": "tool.call",
            "span_id": "call-1",
            "duration_ms": 12,
            "attributes": {
                "edrak.tool.name": "internal_rag.retrieve",
                "edrak.tool.status": "ok",
                "edrak.tool.result.count": 2,
                "edrak.tool.result.summary": (
                    '{"count":2,"decoded_json":false,"item_keys":[],"item_types":["Evidence"],"type":"array"}'
                ),
                "edrak.tool.result.payload": (
                    '[{"excerpt":"Grounded result","api_key":"do-not-send"}]'
                ),
            },
        }],
    }

    calls, _ = _execution_calls(data)
    assert calls[0]["result_count"] == 2
    assert calls[0]["result_summary"]["type"] == "array"
    assert calls[0]["result_payload"][0]["api_key"] == "do-not-send"

    judge = StubJudge(enabled=True)
    result = eval_execution_trace(
        data,
        "Find evidence.",
        "The answer uses the retrieved result.",
        judge,
        include_payloads=True,
    )
    result_use_judgment = next(call for call in judge.calls if call[0] == "tool_result_use")
    assert result["dimensions"]["tool_result_use"]["status"] == "assessable"
    assert "Grounded result" in result_use_judgment[3][0]
    assert "do-not-send" not in json.dumps(result_use_judgment)
    assert result["metrics"]["call_details"][0]["result_count"] == 2
    assert result["metrics"]["call_details"][0]["result_summary"]["type"] == "array"
    assert result["metrics"]["call_details"][0]["result"] == [
        {"excerpt": "Grounded result", "api_key": "[REDACTED]"}
    ]

    older_trace = eval_execution_trace({
        "spans": [{
            "name": "tool.call",
            "span_id": "old-call",
            "attributes": {
                "edrak.tool.name": "internal_rag.retrieve",
                "edrak.tool.result.summary": '{"count":3,"type":"array"}',
            },
        }],
    }, "", "", StubJudge())
    assert older_trace["metrics"]["call_details"][0]["result_count"] == 3


def test_tool_trace_flags_disallowed_tools_bad_arguments_and_repeated_work() -> None:
    payload = {
        "execution_trace": {
            "allowed_tools": ["search"],
            "expected_sequence": ["fetch", "search"],
            "calls": [
                {
                    "tool": "search",
                    "args": {"query": 12},
                    "argument_schema": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {"query": {"type": "string"}},
                    },
                },
                {"tool": "fetch", "args": {"url": "https://example.com"}},
                {"tool": "fetch", "args": {"url": "https://example.com"}},
            ],
        }
    }

    result = eval_execution_trace(payload, "Find the price.", "", StubJudge())

    assert result["metrics"]["sequence_missing"] == ["search"]
    assert result["metrics"]["schema_errors"] == ["call 1: invalid type for query"]
    assert result["metrics"]["repeated_calls"] == 1
    assert failed_check(result, "tool calls stay within declared/available tools")["passed"] is False
    assert failed_check(result, "tool arguments satisfy supplied schemas")["passed"] is False
    assert failed_check(result, "observed tools follow the expected sequence")["passed"] is False
    assert failed_check(result, "trace avoids duplicate tool work")["passed"] is False


def test_telemetry_extracts_tool_spans_and_correlates_worker_context() -> None:
    data = {
        "allowed_tools": ["internal_rag.retrieve"],
        "expected_sequence": ["internal_rag.retrieve"],
        "spans": [
            {
                "name": "edrak.worker.run",
                "span_id": "worker-span",
                "status": "ok",
                "duration_ms": 100,
                "attributes": {
                    "edrak.agent.type": "internal_intelligence",
                    "edrak.task.id": "task-1",
                    "edrak.task.attempt": 1,
                },
            },
            {
                "name": "tool.call",
                "span_id": "tool-span",
                "parent_span_id": "worker-span",
                "status": "error",
                "duration_ms": 25,
                "attributes": {
                    "edrak.tool.name": "internal_rag.retrieve",
                    "edrak.tool.status": "error",
                    "edrak.tool.error_code": "retrieval_timeout",
                    "edrak.tool.fallback": True,
                },
            },
        ],
    }

    result = eval_telemetry(Path("telemetry.json"), data, Ctx(), StubJudge(), make_cfg())

    assert set(result["metrics"]) == {"worker_types", "tool_names", "units", "units_flagged"}
    assert result["metrics"]["tool_names"] == ["internal_rag.retrieve"]
    tool_row = next(row for row in result["rows"] if row["unit_id"] == "tool-span")
    assert tool_row["worker"] == "internal_intelligence"
    assert tool_row["task_id"] == "task-1"
    assert result["execution_quality"]["metrics"]["errors"] == 1


def test_task_trace_correlates_telemetry_attempts_with_string_ids() -> None:
    agent = EvalAgent.__new__(EvalAgent)
    agent.loaded = [
        ("telemetry.json", "telemetry", {
            "spans": [
                {"name": "edrak.worker.run", "span_id": "worker", "attributes": {
                    "edrak.agent.type": "internal_intelligence",
                    "edrak.task.id": "task-1",
                    "edrak.task.attempt": "2",
                }},
                {"name": "tool.call", "span_id": "call", "parent_span_id": "worker", "attributes": {
                    "edrak.tool.name": "internal_rag.retrieve",
                    "edrak.tool.status": "ok",
                    "edrak.tool.argument.names": "query",
                    "edrak.tool.argument.types": '{"query":"str"}',
                }},
            ],
        }),
    ]

    calls = agent.telemetry_calls_for({"task_id": "task-1", "attempt": 2})

    assert len(calls) == 1
    assert calls[0]["tool"] == "internal_rag.retrieve"
    assert calls[0]["worker"] == "internal_intelligence"
    assert calls[0]["attempt"] == "2"


def test_rag_trace_correlates_only_tool_calls_for_its_query() -> None:
    agent = EvalAgent.__new__(EvalAgent)
    agent.loaded = [
        ("telemetry.json", "telemetry", {
            "spans": [
                {"name": "edrak.worker.run", "span_id": "worker", "attributes": {
                    "edrak.agent.type": "internal_intelligence",
                    "edrak.task.id": "task-1",
                    "edrak.task.attempt": 1,
                }},
                {"name": "tool.call", "span_id": "first", "parent_span_id": "worker", "attributes": {
                    "edrak.tool.name": "internal_rag.retrieve",
                    "edrak.tool.argument.values": '{"query":"first query"}',
                }},
                {"name": "tool.call", "span_id": "second", "parent_span_id": "worker", "attributes": {
                    "edrak.tool.name": "internal_rag.retrieve",
                    "edrak.tool.argument.values": '{"query":"second query"}',
                }},
            ],
        }),
    ]

    calls = agent.telemetry_calls_for({
        "worker": "internal_intelligence",
        "task_id": "task-1",
        "attempt": 1,
        "query": "second query",
        "retrieved_chunks": [],
    })

    assert [call["span_id"] for call in calls] == ["second"]


def test_execution_quality_uses_local_argument_fingerprint_without_exposing_values() -> None:
    class RecordingJudge(StubJudge):
        def run(self, metric, question, output, context, threshold, spec=None):
            self.calls.append((metric, question, output, context, threshold, spec))
            return {"score": 0.9, "reason": "ok", "error": ""}

    judge = RecordingJudge(enabled=True)
    payload = {"execution_trace": {
        "complete": True,
        "allowed_tools": ["search"],
        "expected_sequence": ["search"],
        "calls": [
            {"tool": "search", "status": "ok", "argument_values": {"query": "private query"}},
            {"tool": "search", "status": "ok", "argument_values": {"query": "private query"}},
        ],
    }}

    result = eval_execution_trace(payload, "Find evidence.", "answer", judge)

    assert result["metrics"]["repeated_calls"] == 1
    assert result["dimensions"]["efficiency"]["status"] == "assessable"
    assert "private query" not in json.dumps(judge.calls)
    assert "tool_selection" not in [call[0] for call in judge.calls]


def test_orchestration_counts_partial_worker_output_as_completed_execution() -> None:
    data = {
        "status": "completed",
        "plan": {"tasks": [{"worker": "a"}, {"worker": "b"}]},
        "results": [
            {"worker": "a", "status": "completed", "attempt": 1},
            {
                "worker": "b",
                "status": "partial",
                "error": "Some research tasks were skipped.",
                "findings": [{"statement": "Partial but usable result."}],
                "evidence": [{"evidence_id": "e-1"}],
                "attempt": 2,
            },
        ],
    }
    ctx = Ctx(run_manifest={
        "status": "cancelled",
        "started_at_utc": "2026-10-17T08:00:00+00:00",
        "completed_at_utc": "2026-10-17T08:02:00+00:00",
    })

    result = eval_orchestration(Path("orchestration.json"), data, ctx, StubJudge(), make_cfg())

    assert result["metrics"] == {
        "planned_tasks": 2,
        "result_tasks": 2,
        "successful_tasks": 2,
        "task_completion_pct": 100.0,
        "workflow_success_pct": 100.0,
    }


def test_orchestration_counts_failed_agent_without_output_as_failure() -> None:
    data = {
        "status": "completed",
        "plan": {"tasks": [{"worker": "a"}, {"worker": "b"}]},
        "results": [
            {
                "worker": "a",
                "status": "partial",
                "error": "Some research tasks were skipped.",
                "findings": [{"statement": "Partial but usable result."}],
                "evidence": [{"evidence_id": "e-1"}],
            },
            {"worker": "b", "status": "failed", "error": "Worker crashed."},
        ],
    }

    result = eval_orchestration(Path("orchestration.json"), data, Ctx(), StubJudge(), make_cfg())

    assert result["metrics"]["successful_tasks"] == 1
    assert result["metrics"]["task_completion_pct"] == 50.0
    assert set(result["metrics"]) == {
        "planned_tasks", "result_tasks", "successful_tasks", "task_completion_pct", "workflow_success_pct"
    }
    failed_row = next(row for row in result["rows"] if row["unit_id"] == "b")
    assert "task_failed" in failed_row["flags"]


def test_orchestration_omits_retry_metrics_not_shown_in_dashboard() -> None:
    data = {
        "status": "completed",
        "plan": {"tasks": [{"task_id": "task-a"}, {"task_id": "task-b"}]},
        "results": [
            {"task_id": "task-a", "status": "completed", "attempt": 1},
            {"task_id": "task-b", "status": "completed", "attempt": 2},
        ],
    }
    ctx = Ctx(run_manifest={
        "status": "completed",
        "artifacts": {"worker:task-b:1": "worker-a.json", "worker:task-b:2": "worker-b.json"},
    })

    result = eval_orchestration(Path("orchestration.json"), data, ctx, StubJudge(), make_cfg())

    assert result["metrics"]["successful_tasks"] == 2
    assert result["metrics"]["task_completion_pct"] == 100.0
    assert result["metrics"]["workflow_success_pct"] == 100.0
    assert not any("retry" in key for key in result["metrics"])


def test_orchestration_omits_replan_metrics_not_shown_in_dashboard() -> None:
    data = {
        "status": "completed",
        "plan": {"tasks": [
            {"task_id": "internal-new", "worker": "internal_intelligence"},
            {"task_id": "competitor-new", "worker": "competitor_intelligence"},
            {"task_id": "market-new", "worker": "market_intelligence"},
            {"task_id": "customer-new", "worker": "customer_trends"},
        ]},
        "results": [
            {"task_id": "internal-new", "worker": "internal_intelligence", "status": "completed"},
            {"task_id": "competitor-new", "worker": "competitor_intelligence", "status": "partial"},
            {"task_id": "market-new", "worker": "market_intelligence", "status": "completed"},
            {"task_id": "customer-new", "worker": "customer_trends", "status": "partial"},
        ],
    }
    ctx = Ctx(run_manifest={
        "status": "cancelled",
        "stages": [
            {"node": "dispatch", "artifacts": [
                "inputs/worker_internal_intelligence_attempt_1_internal-old.json",
                "inputs/worker_competitor_intelligence_attempt_1_competitor-old.json",
                "inputs/worker_market_intelligence_attempt_1_market-old.json",
            ]},
            {"node": "replan", "artifacts": ["context/plan.json"]},
            {"node": "dispatch", "artifacts": [
                "inputs/worker_internal_intelligence_attempt_1_internal-new.json",
                "inputs/worker_competitor_intelligence_attempt_1_competitor-new.json",
                "inputs/worker_market_intelligence_attempt_1_market-new.json",
                "inputs/worker_customer_trends_attempt_1_customer-new.json",
            ]},
        ],
    })

    result = eval_orchestration(Path("orchestration.json"), data, ctx, StubJudge(), make_cfg())

    assert result["metrics"]["successful_tasks"] == 4
    assert result["metrics"]["task_completion_pct"] == 100.0
    assert result["metrics"]["workflow_success_pct"] == 100.0
    assert not any("retry" in key or "partial" in key for key in result["metrics"])


def test_metrics_stage_exports_only_dashboard_unit_counts() -> None:
    result = eval_metrics(
        Path("metrics.json"),
        {"results": []},
        Ctx(),
        StubJudge(),
        make_cfg(),
    )

    assert result["metrics"] == {"units": 0, "units_flagged": 0}


def test_decision_stages_omit_non_dashboard_aggregate_metrics() -> None:
    cfg = make_cfg()
    judge = StubJudge()
    verification = eval_verification(Path("verification.json"), {}, Ctx(), judge, cfg)
    cross_signal = eval_cross_signal(Path("cross_signal.json"), {}, Ctx(), judge, cfg)
    decision = eval_decision(Path("decision.json"), {}, Ctx(), judge, cfg)

    for result in (verification, cross_signal, decision):
        assert not {
            "verified_pct", "false_verification_pct", "confidence_stdev", "control_gaps",
            "signals_on_unverified_findings", "finding_usage_pct", "signal_coverage_pct",
            "options_without_content_pct", "human_approval_pct", "near_duplicate_actions",
        }.intersection(result["metrics"])
        assert dashboard_metrics(result["metrics"])


def test_pipeline_keeps_rerun_integrity_checks_without_detail_payloads() -> None:
    loaded = [
        (Path("worker_attempt_1.json"), "worker", {
            "worker": "competitor_intelligence",
            "attempt": 1,
            "findings": [{"finding_id": "F1", "statement": "Original statement."}],
        }),
        (Path("worker_attempt_2.json"), "worker", {
            "worker": "competitor_intelligence",
            "attempt": 2,
            "findings": [{"finding_id": "F1", "statement": "Changed statement."}],
        }),
    ]

    result = pipeline_checks(Ctx(), [], loaded)

    assert set(result) == {"checks"}
    assert any(
        check["check"].startswith("re-run of competitor_intelligence")
        and check["passed"] is False
        for check in result["checks"]
    )


def test_execution_trace_reports_provider_and_argument_types_and_nullable_domains() -> None:
    data = {
        "spans": [
            {"name": "edrak.worker.run", "span_id": "w", "attributes": {
                "edrak.agent.type": "competitor_intelligence", "edrak.task.id": "t1", "edrak.task.attempt": 1}},
            {"name": "tool.call", "span_id": "c", "parent_span_id": "w", "status": "ok", "attributes": {
                "edrak.tool.name": "competitor_web_search",
                "edrak.tool.provider": "tavily",
                "edrak.tool.status": "ok",
                "edrak.tool.argument.names": "domains,query,stage",
                "edrak.tool.argument.types": '{"domains":"NoneType","query":"str","stage":"int"}',
            }},
        ],
    }
    calls, _ = _execution_calls(data)
    assert calls[0]["provider"] == "tavily"
    assert calls[0]["attempt"] == 1

    result = eval_execution_trace(
        {"worker": "competitor_intelligence", "execution_trace": {"calls": calls}}, "", "", StubJudge()
    )
    assert result["metrics"]["schema_errors"] == []
    detail = result["metrics"]["call_details"][0]
    assert detail["provider"] == "tavily"
    assert detail["argument_types"] == {"domains": "NoneType", "query": "str", "stage": "int"}


def test_worker_output_metrics_check_evidence_grounding_and_confidence() -> None:
    data = {
        "worker": "internal_intelligence",
        "status": "completed",
        "confidence": 0.8,
        "metadata": {"research_goal": "Assess internal AI capabilities.", "evidence_count": 1},
        "evidence": [
            {
                "evidence_id": "E1",
                "excerpt": "The company operates two AI data centers.",
                "source_url": "https://internal.example.com/architecture",
                "metadata": {"first_party": True, "score": 0.9},
            }
        ],
        "findings": [
            {
                "finding_id": "F1",
                "statement": "The company operates two AI data centers.",
                "confidence": 0.8,
                "evidence_ids": ["E1"],
                "limitations": [],
            }
        ],
    }

    result = eval_worker(
        Path("worker.json"),
        data,
        Ctx(goal="Assess internal AI capabilities."),
        StubJudge(),
        make_cfg(),
    )

    assert result["metrics"]["units"] == len(result["rows"])
    assert not any(key in result["metrics"] for key in (
        "findings", "evidence_in_file", "evidence_cited", "evidence_utilization_pct",
        "agent_confidence", "avg_finding_confidence",
    ))
    finding = next(row for row in result["rows"] if row["unit_type"] == "finding")
    assert finding["refs"] == finding["refs_resolved"] == 1
    assert finding["faithfulness_score"] is None
    assert failed_check(result, "every finding is grounded (has refs)")["passed"] is True
    assert failed_check(result, "every cited evidence id resolves")["passed"] is True


def test_worker_evaluation_ignores_comparison_matrix_cells() -> None:
    data = {
        "worker": "competitor_intelligence",
        "status": "completed",
        "confidence": 0.8,
        "metadata": {
            "research_goal": "Compare competitor capabilities and availability.",
            "competitors": ["Intel Corp"],
            "competitor_profiles": {"Intel Corp": {"overview": "Intel develops AI accelerators."}},
            "comparison_matrix": {
                "dimensions": [
                    {
                        "dimension": "Core capabilities relevant to the goal",
                        "cells": [
                            {
                                "competitor": "Intel Corp",
                                "relative_to_target": "stronger",
                                "summary": "No performance benchmark was provided.",
                            }
                        ],
                    }
                ]
            },
        },
        "evidence": [],
        "findings": [],
    }

    result = eval_worker(
        Path("worker.json"),
        data,
        Ctx(goal="Compare competitor capabilities and availability."),
        StubJudge(),
        make_cfg(),
    )

    assert not any(row["unit_type"] == "matrix_cell" for row in result["rows"])
    assert "matrix_cells" not in result["metrics"]
    assert "matrix_cells_with_evidence_pct" not in result["metrics"]
    assert "matrix_cells_not_established_pct" not in result["metrics"]
    assert not any(check["check"] == "matrix claims rest on evidence" for check in result["checks"])


def test_runner_omits_unused_exports_and_cleans_stale_reports(tmp_path: Path, capsys) -> None:
    source = tmp_path / "rag.json"
    source.write_text(
        json.dumps(
            {
                "query": "How long is the return period?",
                "answer": "Returns are accepted for 30 days.",
                "retrieved_documents": [
                    {
                        "document_id": "D1",
                        "content": "Returns are accepted for 30 days.",
                        "metadata": {"relevance_score": 0.9},
                        "source": "https://policy.example.com/returns",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    metrics_source = tmp_path / "metrics.json"
    metrics_source.write_text(
        json.dumps({"metrics_stage": True, "results": [{"metadata": {"metrics": [1]}}]}),
        encoding="utf-8",
    )
    out = tmp_path / "reports"
    out.mkdir()
    (out / "metrics_report.json").write_text("stale report", encoding="utf-8")
    (out / "metrics_units.csv").write_text("stale units", encoding="utf-8")
    (out / "rag_units.csv").write_text("stale unit details", encoding="utf-8")
    (out / "overview.csv").write_text("stale overview", encoding="utf-8")
    (out / "summary.md").write_text("stale summary", encoding="utf-8")
    (out / "observability.json").write_text("stale observability", encoding="utf-8")
    args = Namespace(
        inputs=[str(source), str(metrics_source)],
        out=str(out),
        agents=None,
        task="",
        model="test-model",
        dry_run=True,
        deep=False,
        context_relevancy=False,
        no_extra_metrics=False,
        include_tool_payloads=False,
        faithfulness_threshold=0.7,
        relevancy_threshold=0.5,
        context_relevancy_threshold=0.5,
        specificity_threshold=0.5,
        insight_threshold=0.5,
        actionability_threshold=0.5,
    )
    agent = EvalAgent(args)

    assert agent.load() is True
    assert agent.run() == 0

    output = capsys.readouterr().out
    rag_result = agent.per_agent["rag"][0]
    integrity_checks = [item for item in rag_result["checks"] if item["severity"] == "error"]
    integrity_failures = sum(not item["passed"] for item in integrity_checks)
    integrity_status = "FAIL" if integrity_failures else "PASS"
    units = len(rag_result["rows"])
    flagged_units = sum(bool(row.get("flags")) for row in rag_result["rows"])
    assert (
        f"Finished evaluating rag: integrity {integrity_status} "
        f"({integrity_failures}/{len(integrity_checks)} failed); "
        f"units {units} ({flagged_units} flagged)."
    ) in output

    report = json.loads((tmp_path / "reports" / "rag_report.json").read_text(encoding="utf-8"))
    metrics = report["files"][0]["metrics"]
    assert report["dry_run"] is True
    assert "metrics_used" not in report["files"][0]
    assert "queries" not in metrics
    assert "retrieved_chunks" not in metrics
    assert "mean_retrieval_score" not in metrics
    assert "context_relevancy_reason" not in metrics
    assert "execution_quality_status" not in metrics
    assert set(report["files"][0]["execution_quality"]) == {"dimensions", "metrics"}
    assert set(report["files"][0]["execution_quality"]["metrics"]) == {
        "calls", "errors", "repeated_calls", "call_details"
    }
    assert "metrics_report.json" not in {p.name for p in out.iterdir()}
    assert "metrics_units.csv" not in {p.name for p in out.iterdir()}
    assert "rag_units.csv" not in {p.name for p in out.iterdir()}
    assert "overview.csv" not in {p.name for p in out.iterdir()}
    assert "summary.md" not in {p.name for p in out.iterdir()}
    assert "observability.json" not in {p.name for p in out.iterdir()}
    pipeline_report = json.loads((out / "pipeline_report.json").read_text(encoding="utf-8"))
    assert set(pipeline_report) == {"total", "passed", "failed", "warnings", "checks"}
    pipeline_checks = agent.pipeline["checks"]
    assert pipeline_report == {
        "total": len(pipeline_checks),
        "passed": sum(check["passed"] for check in pipeline_checks),
        "failed": sum(
            not check["passed"] and check["severity"] == "error"
            for check in pipeline_checks
        ),
        "warnings": sum(
            not check["passed"] and check["severity"] != "error"
            for check in pipeline_checks
        ),
        "checks": pipeline_checks,
    }
    assert "metrics" not in agent.per_agent


def test_runner_omits_execution_quality_for_non_tool_stages(tmp_path: Path, monkeypatch) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    stage_data = {
        "verification": {"findings": [], "control_summary": {}},
        "cross_signal": {"signals": [], "decision_ready_context": {}},
        "decision": {"question_recommendations": []},
        "orchestration": {"plan": {}, "results": []},
    }
    for kind, data in stage_data.items():
        (inputs / f"{kind}.json").write_text(json.dumps(data), encoding="utf-8")

        def evaluate(path, data, ctx, judge, cfg):
            return {"rows": [], "metrics": {}, "checks": [], "metrics_used": []}

        monkeypatch.setitem(evaluator_module.EVALUATORS, kind, evaluate)

    def unexpected_execution_evaluation(*args, **kwargs):
        raise AssertionError("Execution quality should not run for these stages.")

    monkeypatch.setattr(evaluator_module, "eval_execution_trace", unexpected_execution_evaluation)
    out = tmp_path / "reports"
    out.mkdir()
    args = Namespace(
        inputs=[str(inputs / "*.json")],
        out=str(out),
        agents=None,
        task="",
        model="test-model",
        dry_run=True,
        deep=False,
        context_relevancy=False,
        no_extra_metrics=False,
        include_tool_payloads=False,
        faithfulness_threshold=0.7,
        relevancy_threshold=0.5,
        context_relevancy_threshold=0.5,
        specificity_threshold=0.5,
        insight_threshold=0.5,
        actionability_threshold=0.5,
    )
    agent = EvalAgent(args)

    assert agent.load() is True
    assert agent.run() == 0

    for kind in stage_data:
        report = json.loads((out / f"{kind}_report.json").read_text(encoding="utf-8"))
        file_report = report["files"][0]
        assert "execution_quality" not in file_report
        assert "execution_quality_status" not in file_report["metrics"]
        assert not any(check["check"] == "execution quality has structured trace evidence"
                       for check in file_report["checks"])


def test_langgraph_entrypoint_runs_the_same_evaluator(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "rag.json"
    source.write_text(
        json.dumps(
            {
                "query": "What is the return period?",
                "retrieved_chunks": [
                    {
                        "chunk_id": "chunk-1",
                        "text": "Returns are accepted for 30 days.",
                        "score": 0.9,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "graph-report"
    monkeypatch.setattr(
        "sys.argv",
        [
            "edrak_eval_graph_enhanced.py",
            "--inputs",
            str(source),
            "--out",
            str(output),
            "--dry-run",
        ],
    )

    assert graph_main() == 0
    assert (output / "rag_report.json").is_file()


def test_compare_to_rejects_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "edrak_eval_agent_enhanced.py",
            "--run-bundle",
            str(tmp_path / "unused"),
            "--dry-run",
            "--compare-to",
            "nvidia_product_launch_inputs",
        ],
    )

    try:
        evaluator_module.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected --compare-to with --dry-run to be rejected.")


def test_keep_final_uses_worker_task_ids_from_final_orchestration() -> None:
    agent = object.__new__(EvalAgent)
    old_worker = Path("worker_competitor_intelligence_attempt_1_old.json")
    final_worker = Path("worker_competitor_intelligence_attempt_1_final.json")
    old_rag = Path("rag_old.json")
    final_rag = Path("rag_final.json")
    orchestration = Path("orchestration_result.json")
    agent.loaded = [
        (
            old_worker,
            "worker",
            {
                "worker": "competitor_intelligence",
                "task_id": "old-task",
                "attempt": 1,
                "findings": [{"finding_id": "old-finding"}],
            },
        ),
        (old_rag, "rag", {"task_id": "old-task"}),
        (
            final_worker,
            "worker",
            {
                "worker": "competitor_intelligence",
                "task_id": "final-task",
                "attempt": 1,
                "findings": [{"finding_id": "final-finding"}],
            },
        ),
        (final_rag, "rag", {"task_id": "final-task"}),
        (
            orchestration,
            "orchestration",
            {
                "plan": {"tasks": [{"task_id": "final-task"}]},
                "results": [
                    {
                        "worker": "competitor_intelligence",
                        "task_id": "final-task",
                        "attempt": 1,
                    }
                ],
            },
        ),
    ]

    agent.keep_final()

    kept_paths = {path for path, _, _ in agent.loaded}
    assert final_worker in kept_paths
    assert final_rag in kept_paths
    assert orchestration in kept_paths
    assert old_worker not in kept_paths
    assert old_rag not in kept_paths


def test_keep_final_uses_orchestrated_retry_attempt_for_same_task() -> None:
    agent = object.__new__(EvalAgent)
    first_attempt = Path("worker_competitor_intelligence_attempt_1_task.json")
    final_attempt = Path("worker_competitor_intelligence_attempt_2_task.json")
    orchestration = Path("orchestration_result.json")
    agent.loaded = [
        (
            first_attempt,
            "worker",
            {
                "worker": "competitor_intelligence",
                "task_id": "same-task",
                "attempt": 1,
                "findings": [{"finding_id": "first-finding"}],
            },
        ),
        (
            final_attempt,
            "worker",
            {
                "worker": "competitor_intelligence",
                "task_id": "same-task",
                "attempt": 2,
                "findings": [{"finding_id": "final-finding"}],
            },
        ),
        (
            orchestration,
            "orchestration",
            {
                "plan": {"tasks": [{"task_id": "same-task"}]},
                "results": [
                    {
                        "worker": "competitor_intelligence",
                        "task_id": "same-task",
                        "attempt": 2,
                    }
                ],
            },
        ),
    ]

    agent.keep_final()

    kept_paths = {path for path, _, _ in agent.loaded}
    assert final_attempt in kept_paths
    assert first_attempt not in kept_paths
    assert orchestration in kept_paths
