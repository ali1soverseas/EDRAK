from __future__ import annotations

import json
import threading
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import Evaluation.evaluation_Agent.edrak_eval_agent_enhanced as evaluator_module
from Evaluation.evaluation_Agent.edrak_eval_agent_enhanced import (
    Cfg,
    Ctx,
    EvalAgent,
    Judge,
    detect,
    _execution_calls,
    _golden_answer_for_rag,
    eval_execution_trace,
    eval_orchestration,
    eval_rag,
    eval_telemetry,
    eval_worker,
)
from Evaluation.evaluation_Agent.edrak_eval_graph_enhanced import main as graph_main


class StubJudge:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[tuple[str, str, str, list[str], float, tuple | None]] = []
        self.successes = 0
        self.hits = 0
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

    def flush(self) -> None:
        return None


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

    judge = Judge("test-model", tmp_path / "cache.json", enabled=True, retries=2)
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

    judge = Judge("test-model", tmp_path / "cache.json", enabled=True, retries=2)
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

    judge = Judge("test-model", tmp_path / "cache.json", enabled=True, retries=2)
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


def test_judge_defers_cache_writes_until_flush(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache.json"
    judge = Judge("test-model", cache, enabled=True, retries=1)
    monkeypatch.setattr(judge, "_measure", lambda *a: {"score": 0.9, "reason": "ok", "error": ""})

    judge.run("faithfulness", "q", "answer", ["evidence"], 0.7)
    assert not cache.exists()

    judge.flush()
    assert len(json.loads(cache.read_text(encoding="utf-8"))) == 1
    judge.run("faithfulness", "q", "answer", ["evidence"], 0.7)   # served from cache
    assert judge.calls == 1 and judge.hits == 1


def test_concurrent_judges_flush_cache_with_unique_temporary_files(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache.json"
    judges = [
        Judge("test-model", cache, enabled=True, retries=1),
        Judge("test-model", cache, enabled=True, retries=1),
    ]
    for index, judge in enumerate(judges):
        judge.cache[f"entry-{index}"] = {"score": index}
        judge._unsaved = 1

    replace = evaluator_module.os.replace
    temporary_files: list[Path] = []

    def replace_together(source: str | Path, destination: str | Path) -> None:
        temporary_files.append(Path(source))
        replace(source, destination)

    monkeypatch.setattr(evaluator_module.os, "replace", replace_together)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(judge.flush) for judge in judges]
        for future in futures:
            future.result()

    assert json.loads(cache.read_text(encoding="utf-8")) in (
        {"entry-0": {"score": 0}},
        {"entry-1": {"score": 1}},
    )
    assert len(temporary_files) == 2
    assert len(set(temporary_files)) == 2
    assert not list(tmp_path.glob(".cache.json.*.tmp"))


def test_judge_cache_key_includes_threshold(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache.json"
    judge = Judge("test-model", cache, enabled=True, retries=1)
    measured_thresholds: list[float] = []

    def measure(metric, question, output, context, threshold, *args):
        measured_thresholds.append(threshold)
        return {"score": 0.9, "reason": "ok", "error": ""}

    monkeypatch.setattr(judge, "_measure", measure)

    judge.run("faithfulness", "q", "answer", ["evidence"], 0.7)
    judge.run("faithfulness", "q", "answer", ["evidence"], 0.8)

    assert measured_thresholds == [0.7, 0.8]
    assert judge.calls == 2 and judge.hits == 0


def test_judge_unit_runs_metrics_in_parallel_and_keeps_column_order(tmp_path: Path, monkeypatch) -> None:
    import threading
    from Evaluation.evaluation_Agent.edrak_eval_agent_enhanced import judge_unit

    judge = Judge("test-model", tmp_path / "cache.json", enabled=True, retries=1, workers=3)
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

    assert result["metrics"]["retrieved_chunks"] == 2
    assert result["metrics"]["mean_retrieval_score"] == 0.8
    assert result["metrics"]["invalid_retrieval_scores"] == 1
    assert result["metrics"]["unique_source_domains"] == 2
    assert result["metrics"]["duplicate_chunk_pairs"] == 1
    assert result["metrics"]["context_relevancy_score"] == 0.9
    assert "answer_relevancy_score" not in result["metrics"]
    assert "faithfulness_score" not in result["metrics"]
    assert len(judge.calls) == 1
    assert failed_check(result, "retrieval scores are within [0, 1]")["passed"] is False
    assert failed_check(result, "retrieved chunks are not near-duplicates")["passed"] is False
    assert any(row["unit_type"] == "rag_answer" for row in result["rows"])


def test_rag_retrieval_latency_is_correlated_by_query() -> None:
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

    assert result["metrics"]["retrieval_latency_calls"] == 1
    assert result["metrics"]["retrieval_latency_ms"] == 25


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
    assert result["metrics"]["answer_source"] is None
    assert result["metrics"]["answer_source"] is None
    assert "faithfulness_score" not in result["metrics"]
    assert "answer_relevancy_score" not in result["metrics"]
    assert not any("faithfulness" in metric or metric == "relevancy" for metric in result["metrics_used"])
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
        cache=str(tmp_path / "cache.json"),
        dry_run=False,
        deep=False,
        context_relevancy=False,
        no_extra_metrics=False,
        include_tool_payloads=False,
        narrative=False,
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
    assert rag_result["metrics"]["answer_source"] is None
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

    assert result["metrics"]["expected_output_source"].startswith("golden/ground_truth/")
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

    assert result["metrics"]["spans"] == 2
    assert result["metrics"]["tool_calls"] == 1
    assert result["metrics"]["observed_errors"] == 1
    assert result["metrics"]["worker_task_identity_pct"] == 100
    assert result["metrics"]["worker_process_recorded_pct"] == 100
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

    assert result["metrics"]["successful_tasks"] == 2
    assert result["metrics"]["task_completion_pct"] == 100.0
    assert result["metrics"]["workflow_success_pct"] == 100.0
    assert result["metrics"]["failed_tasks"] == 0
    assert result["metrics"]["partial_tasks"] == 1
    assert result["metrics"]["incomplete_tasks"] == 0
    assert result["metrics"]["failure_rate_pct"] == 0.0
    assert result["metrics"]["retry_events"] == 1
    assert result["metrics"]["successfully_retried_tasks"] == 1
    assert result["metrics"]["retry_rate_pct"] == 50.0
    assert result["metrics"]["execution_duration_seconds"] == 120.0
    assert result["metrics"]["dependency_integrity_pct"] is None


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
    assert result["metrics"]["failed_tasks"] == 1
    assert result["metrics"]["failure_rate_pct"] == 50.0
    assert result["metrics"]["partial_tasks"] == 1
    failed_row = next(row for row in result["rows"] if row["unit_id"] == "b")
    assert "task_failed" in failed_row["flags"]


def test_orchestration_retry_rate_counts_only_replanned_tasks_that_succeeded() -> None:
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
    assert result["metrics"]["retry_events"] == 1
    assert result["metrics"]["successfully_retried_tasks"] == 1
    assert result["metrics"]["retry_rate_pct"] == 50.0


def test_orchestration_replan_matches_replaced_task_ids_by_worker() -> None:
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
    assert result["metrics"]["partial_tasks"] == 2
    assert result["metrics"]["retry_events"] == 1
    assert result["metrics"]["successfully_retried_tasks"] == 3
    assert result["metrics"]["retry_rate_pct"] == 75.0


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

    assert result["metrics"]["findings"] == 1
    assert result["metrics"]["evidence_in_file"] == 1
    assert result["metrics"]["evidence_cited"] == 1
    assert result["metrics"]["evidence_utilization_pct"] == 100
    assert result["metrics"]["agent_confidence"] == 0.8
    assert result["metrics"]["avg_finding_confidence"] == 0.8
    finding = next(row for row in result["rows"] if row["unit_type"] == "finding")
    assert finding["refs"] == finding["refs_resolved"] == 1
    assert finding["faithfulness_score"] is None
    assert failed_check(result, "every finding is grounded (has refs)")["passed"] is True
    assert failed_check(result, "every cited evidence id resolves")["passed"] is True


def test_runner_skips_metrics_stage_report_and_cleans_stale_metrics_files(tmp_path: Path) -> None:
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
    args = Namespace(
        inputs=[str(source), str(metrics_source)],
        out=str(out),
        agents=None,
        task="",
        model="test-model",
        cache=str(tmp_path / "cache.json"),
        dry_run=True,
        deep=False,
        context_relevancy=False,
        no_extra_metrics=False,
        include_tool_payloads=False,
        narrative=False,
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

    report = json.loads((tmp_path / "reports" / "rag_report.json").read_text(encoding="utf-8"))
    metrics = report["files"][0]["metrics"]
    assert report["dry_run"] is True
    assert "rag_retrieval_quality" in report["files"][0]["metrics_used"]
    assert metrics["queries"] == 1
    assert metrics["retrieved_chunks"] == 1
    assert metrics["mean_retrieval_score"] == 0.9
    assert metrics["execution_quality_status"] == "not_assessable"
    overview = (tmp_path / "reports" / "overview.csv").read_text(encoding="utf-8-sig")
    assert "retrieved_chunks" in overview
    assert "metrics_report.json" not in {p.name for p in out.iterdir()}
    assert "metrics_units.csv" not in {p.name for p in out.iterdir()}
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
        cache=str(tmp_path / "cache.json"),
        dry_run=True,
        deep=False,
        context_relevancy=False,
        no_extra_metrics=False,
        include_tool_payloads=False,
        narrative=False,
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
