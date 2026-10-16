from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from evaluationEnhanced.evaluation_Agent.edrak_eval_agent_enhanced import (
    Cfg,
    Ctx,
    EvalAgent,
    detect,
    eval_execution_trace,
    eval_rag,
    eval_telemetry,
    eval_worker,
)
from evaluationEnhanced.evaluation_Agent.edrak_eval_graph_enhanced import main as graph_main


class StubJudge:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[tuple[str, str, str, list[str], float, tuple | None]] = []

    def run(
        self,
        metric: str,
        question: str,
        output: str,
        context: list[str],
        threshold: float,
        spec: tuple | None = None,
    ) -> dict[str, object]:
        self.calls.append((metric, question, output, context, threshold, spec))
        if not self.enabled:
            return {"score": None, "reason": "", "error": "dry-run"}
        return {"score": 0.9, "reason": "stubbed", "error": ""}


def make_cfg() -> Cfg:
    return Cfg(
        Namespace(
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
    )


def failed_check(result: dict, name: str) -> dict:
    return next(check for check in result["checks"] if check["check"] == name)


def test_rag_evaluation_records_retrieval_and_answer_quality() -> None:
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
    assert result["metrics"]["answer_relevancy_score"] == 0.9
    assert result["metrics"]["faithfulness_score"] == 0.9
    assert len(judge.calls) == 3
    assert failed_check(result, "retrieval scores are within [0, 1]")["passed"] is False
    assert failed_check(result, "retrieved chunks are not near-duplicates")["passed"] is False
    assert any(row["unit_type"] == "rag_answer" for row in result["rows"])


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
    assert len(judge.calls) == 6
    assert all(call[5] is not None for call in judge.calls)
    assert all(
        secret not in str((call[2], call[3]))
        for call in judge.calls
        for secret in ("secret-search-key", "secret-result-token")
    )
    assert failed_check(result, "tool calls stay within declared/available tools")["passed"] is True
    assert failed_check(result, "tool arguments satisfy supplied schemas")["passed"] is True
    assert failed_check(result, "tool failures are handled or explicitly surfaced")["passed"] is True


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


def test_runner_loads_rag_input_and_writes_metrics_report(tmp_path: Path) -> None:
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
    args = Namespace(
        inputs=[str(source)],
        out=str(tmp_path / "reports"),
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
