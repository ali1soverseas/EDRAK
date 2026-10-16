import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from edrak.contracts.task import WorkerType

from evaluationEnhanced.evaluation_Agent import edrak_eval_agent_enhanced as eval_agent


def _cfg():
    return eval_agent.Cfg(
        SimpleNamespace(
            deep=False,
            context_relevancy=False,
            no_extra_metrics=False,
            faithfulness_threshold=0.7,
            relevancy_threshold=0.5,
            context_relevancy_threshold=0.5,
            specificity_threshold=0.5,
            insight_threshold=0.5,
            actionability_threshold=0.5,
        )
    )


class RecordingJudge:
    enabled = False

    def __init__(self):
        self.specs = {}
        self.questions = {}
        self.calls = []

    def run(self, metric, question, output, context, threshold, spec=None):
        self.specs[metric] = spec
        self.questions[metric] = question
        self.calls.append((metric, question, output, context, threshold, spec))
        return {"score": None, "reason": "", "error": "dry-run"}


@pytest.mark.parametrize(
    ("worker", "criteria_phrase"),
    [
        (WorkerType.COMPETITOR_INTELLIGENCE.value, "competitor research finding"),
        (WorkerType.INTERNAL_INTELLIGENCE.value, "internal research finding"),
        (WorkerType.MARKET_INTELLIGENCE.value, "market research finding"),
        (WorkerType.CUSTOMER_TRENDS.value, "customer and trends research finding"),
    ],
)
def test_worker_uses_domain_specific_judge_criteria(worker, criteria_phrase):
    judge = RecordingJudge()
    data = {
        "worker": worker,
        "status": "completed",
        "findings": [
            {
                "finding_id": "finding-1",
                "statement": "A supported finding relevant to the research goal.",
                "category": "other",
                "confidence": 0.8,
                "limitations": ["Evidence is limited to the supplied source."],
                "evidence_refs": [{"evidence_id": "evidence-1", "relation": "supports"}],
            }
        ],
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "source_type": "other",
                "extracted_fact": "The source supports the finding.",
                "excerpt": "The source supports the finding with a concrete detail.",
                "metadata": {},
            }
        ],
        "metadata": {"competitors": ["GitHub"]},
    }

    result = eval_agent.eval_worker(
        Path("worker.json"),
        data,
        eval_agent.Ctx(goal="Assess the business implications."),
        judge,
        _cfg(),
    )

    assert result["metrics"]["findings"] == 1
    assert criteria_phrase in judge.specs["insight"][0]
    assert criteria_phrase in judge.specs["actionability"][0]
    assert "competitor name" not in judge.specs["actionability"][0]
    expected_topic = {
        "competitor_intelligence": "competitor",
        "internal_intelligence": "internal capabilities",
        "market_intelligence": "market, economic",
        "customer_trends": "customer needs",
    }[worker]
    assert expected_topic in judge.questions["relevancy"]
    if worker != WorkerType.COMPETITOR_INTELLIGENCE.value:
        assert "competitor_not_named" not in result["rows"][0]["flags"]


def test_metrics_evaluator_checks_grounding_and_required_outcomes():
    result = eval_agent.eval_metrics(
        Path("metrics.json"),
        {
            "metrics_stage": True,
            "results": [
                {
                    "worker": "competitor_intelligence",
                    "evidence": [
                        {
                            "evidence_id": "e1",
                            "excerpt": "The Team plan costs $19 per user per month.",
                        }
                    ],
                    "metadata": {
                        "metrics": [
                            {
                                "metric_id": "price_per_tier",
                                "entity": "Globex",
                                "value_text": "$19 per user per month",
                                "value": 19.0,
                                "evidence_id": "e1",
                                "source_span": "The Team plan costs $19 per user per month.",
                                "status": "verified",
                            }
                        ],
                        "requirements": [
                            {"metric_id": "price_per_tier", "entity": "Globex", "required": True}
                        ],
                        "requirement_outcomes": [
                            {
                                "metric_id": "price_per_tier",
                                "entity": "Globex",
                                "status": "found",
                            }
                        ],
                    },
                }
            ],
        },
        None,
        None,
        None,
    )

    assert result["metrics"]["verified_observations"] == 1
    assert result["metrics"]["required_outcome_coverage_pct"] == 100.0
    assert all(item["passed"] for item in result["checks"] if item["severity"] == "error")


def test_metrics_evaluator_flags_ungrounded_verified_observation():
    result = eval_agent.eval_metrics(
        Path("metrics.json"),
        {
            "metrics_stage": True,
            "results": [
                {
                    "worker": "competitor_intelligence",
                    "evidence": [{"evidence_id": "e1", "excerpt": "The Team plan costs $19 per user."}],
                    "metadata": {
                        "metrics": [
                            {
                                "metric_id": "price_per_tier",
                                "entity": "Globex",
                                "value_text": "$19 per user",
                                "value": 19.0,
                                "evidence_id": "e1",
                                "source_span": "The Enterprise plan costs $99 per user.",
                                "status": "verified",
                            }
                        ]
                    },
                }
            ],
        },
        None,
        None,
        None,
    )

    integrity_check = next(
        item for item in result["checks"]
        if item["check"] == "verified metric observations are grounded in cited sources"
    )
    assert not integrity_check["passed"]
    assert "source_span_not_in_evidence" in result["rows"][0]["flags"]


def test_metrics_evaluator_rejects_missing_required_outcome():
    result = eval_agent.eval_metrics(
        Path("metrics.json"),
        {
            "metrics_stage": True,
            "results": [
                {
                    "worker": "competitor_intelligence",
                    "metadata": {
                        "metrics": [],
                        "requirements": [
                            {"metric_id": "price_per_tier", "entity": "Globex", "required": True}
                        ],
                        "requirement_outcomes": [],
                    },
                }
            ],
        },
        None,
        None,
        None,
    )

    terminal_check = next(
        item for item in result["checks"]
        if item["check"] == "every required metric has a terminal outcome"
    )
    assert not terminal_check["passed"]
    assert result["metrics"]["required_outcome_coverage_pct"] == 0.0


@pytest.mark.parametrize(
    "payload",
    [
        {"events": [{"name": "pipeline.run", "duration_ms": 10}]},
        {"tool_calls": [{"tool": "web_search", "duration_ms": 10}]},
        {"tool_events": [{"tool": "web_search", "duration_ms": 10}]},
    ],
)
def test_detect_recognizes_event_only_telemetry(payload):
    assert eval_agent.detect(payload) == "telemetry"


def test_detect_keeps_orchestration_payload_with_events_as_orchestration():
    assert eval_agent.detect(
        {"plan": {"tasks": []}, "results": [], "events": [{"name": "pipeline.run"}]}
    ) == "orchestration"


def test_detect_keeps_worker_payload_with_events_as_worker():
    assert eval_agent.detect({"worker": "market_intelligence", "findings": [], "events": []}) == "worker"


def test_telemetry_p95_uses_nearest_rank_for_small_sample():
    result = eval_agent.eval_telemetry(
        Path("telemetry.json"),
        {"events": [{"name": "fast", "duration_ms": 10}, {"name": "slow", "duration_ms": 100}]},
        None,
        None,
        None,
    )

    assert result["metrics"]["spans"] == 2
    assert result["metrics"]["p95_duration_ms"] == 100


def test_telemetry_checks_worker_task_identity_and_correlates_tool_policy():
    result = eval_agent.eval_telemetry(
        Path("telemetry.json"),
        {
            "spans": [
                {
                    "name": "edrak.worker.run",
                    "span_id": "worker-1",
                    "duration_ms": 20,
                    "status": "ok",
                    "attributes": {
                        "edrak.agent.type": "competitor_intelligence",
                        "edrak.task.id": "task-1",
                        "edrak.task.attempt": 1,
                    },
                },
                {
                    "name": "tool.call",
                    "span_id": "tool-1",
                    "parent_span_id": "worker-1",
                    "duration_ms": 10,
                    "status": "ok",
                    "attributes": {
                        "edrak.tool.name": "competitor_web_search",
                        "edrak.tool.argument.names": "query,max_results",
                        "edrak.tool.argument.types": '{"max_results":"int","query":"str"}',
                        "edrak.tool.status": "ok",
                    },
                },
            ]
        },
        None,
        None,
        None,
    )

    assert result["metrics"]["worker_task_identity_pct"] == 100.0
    assert result["metrics"]["worker_process_recorded_pct"] == 100.0
    assert result["execution_quality"]["dimensions"]["tool_selection"]["status"] == "assessable"
    assert result["execution_quality"]["dimensions"]["tool_arguments"]["status"] == "assessable"
    assert result["rows"][1]["worker"] == "competitor_intelligence"
    assert result["rows"][1]["task_id"] == "task-1"
    assert all(
        item["passed"]
        for item in result["checks"]
        if item["check"] in {
            "worker spans carry agent and task identity",
            "worker spans record process status and duration",
        }
    )


def test_telemetry_warns_when_worker_trace_omits_task_id():
    result = eval_agent.eval_telemetry(
        Path("telemetry.json"),
        {
            "spans": [
                {
                    "name": "edrak.worker.run",
                    "span_id": "worker-1",
                    "duration_ms": 20,
                    "status": "ok",
                    "attributes": {"edrak.agent.type": "internal_intelligence"},
                }
            ]
        },
        None,
        None,
        None,
    )

    identity_check = next(
        item for item in result["checks"]
        if item["check"] == "worker spans carry agent and task identity"
    )
    assert not identity_check["passed"]
    assert "1/1" in identity_check["detail"]
    assert result["rows"][0]["flags"] == ["missing_worker_task_identity"]


def test_cli_evaluates_a_run_bundle_without_manual_input_glob(tmp_path, monkeypatch):
    bundle = tmp_path / "analysis-1"
    inputs = bundle / "inputs"
    inputs.mkdir(parents=True)
    outputs = bundle / "outputs"
    outputs.mkdir()
    (bundle / "manifest.json").write_text('{"analysis_id": "analysis-1"}', encoding="utf-8")
    (inputs / "telemetry.json").write_text(
        '{"spans": [{"name": "edrak.orchestration.graph", "trace_id": "trace-1", "span_id": "span-1", '
        '"status": "ok", "duration_ms": 10, "attributes": {}}]}',
        encoding="utf-8",
    )
    (outputs / "metrics_001.json").write_text(
        '{"metrics_stage": true, "results": []}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "edrak_eval_agent_enhanced.py",
            "--run-bundle",
            str(bundle),
            "--dry-run",
        ],
    )

    assert eval_agent.main() == 0
    assert (bundle / "evaluation" / "telemetry_report.json").is_file()
    assert (bundle / "evaluation" / "metrics_report.json").is_file()


def test_cli_returns_failure_when_deterministic_integrity_check_fails(tmp_path, monkeypatch):
    source = tmp_path / "market-worker.json"
    source.write_text(
        json.dumps(
            {
                "worker": "market_intelligence",
                "status": "completed",
                "findings": [],
                "evidence": [],
                "metadata": {},
                "execution_trace": {
                    "calls": [
                        {
                            "tool": "market_search",
                            "args": {"queries": "not-an-array"},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "edrak_eval_agent_enhanced.py",
            "--inputs",
            str(source),
            "--out",
            str(tmp_path / "report"),
            "--dry-run",
        ],
    )

    assert eval_agent.main() == 1
    report = json.loads((tmp_path / "report" / "market_intelligence_report.json").read_text(encoding="utf-8"))
    checks = report["files"][0]["checks"]
    assert not next(
        item for item in checks if item["check"] == "tool arguments satisfy supplied schemas"
    )["passed"]


def test_rag_evaluator_checks_retrieval_quality():
    result = eval_agent.eval_rag(
        Path("rag.json"),
        {
            "query": "Which strategic priorities have evidence?",
            "retrieved_chunks": [
                {
                    "chunk_id": "chunk-1",
                    "text": "Paid credit consumption increased 50% sequentially.",
                    "score": 0.91,
                    "source_url": "https://strategy.example/okrs",
                },
                {
                    "chunk_id": "chunk-2",
                    "text": "The enterprise customer cohort grew by 18% year over year.",
                    "score": 0.78,
                    "source_url": "https://research.example/quarterly-results",
                },
            ],
        },
        None,
        None,
        _cfg(),
    )

    assert result["metrics"]["retrieved_chunks"] == 2
    assert result["metrics"]["chunks_with_score_pct"] == 100.0
    assert result["metrics"]["unique_source_domains"] == 2
    assert all(check["passed"] for check in result["checks"])


def test_rag_evaluator_reads_internal_evidence_shape_and_rejects_invalid_scores():
    result = eval_agent.eval_rag(
        Path("rag.json"),
        {
            "query": "What did the internal plan prioritize?",
            "retrieval_results": [
                {
                    "evidence_id": "evidence-1",
                    "extracted_fact": "The plan prioritized enterprise adoption.",
                    "source_url": "internal://strategy",
                    "metadata": {"relevance_score": 0.82},
                },
                {
                    "evidence_id": "evidence-2",
                    "excerpt": "This score is outside the retriever's normalized range.",
                    "metadata": {"relevance_score": 1.2},
                },
            ],
        },
        None,
        None,
        _cfg(),
    )

    assert result["metrics"]["nonempty_chunks_pct"] == 100.0
    assert result["metrics"]["chunks_with_score_pct"] == 100.0
    assert result["metrics"]["mean_retrieval_score"] == 0.82
    assert result["metrics"]["invalid_retrieval_scores"] == 1
    assert "invalid_retrieval_score" in result["rows"][1]["flags"]
    assert not next(
        item for item in result["checks"] if item["check"] == "retrieval scores are within [0, 1]"
    )["passed"]


def test_rag_evaluator_requests_deepeval_context_answer_and_faithfulness_metrics():
    judge = RecordingJudge()
    judge.enabled = True
    result = eval_agent.eval_rag(
        Path("rag.json"),
        {
            "query": "Which strategic priorities have evidence?",
            "answer": "Paid credit consumption increased 50% sequentially.",
            "retrieved_chunks": [
                {
                    "chunk_id": "chunk-1",
                    "text": "Paid credit consumption increased 50% sequentially.",
                    "score": 0.91,
                    "source_url": "https://strategy.example/okrs",
                }
            ],
        },
        None,
        judge,
        _cfg(),
    )

    assert set(judge.questions) == {"context_relevancy", "relevancy", "faithfulness"}
    assert result["metrics_used"] == [
        "rag_retrieval_quality",
        "context_relevancy",
        "relevancy",
        "faithfulness",
    ]
    assert result["rows"][-1]["unit_type"] == "rag_answer"


def test_rag_evaluator_does_not_report_unrun_judge_metrics():
    judge = RecordingJudge()
    judge.enabled = True
    result = eval_agent.eval_rag(
        Path("rag.json"),
        {"query": "What does the internal plan prioritize?", "retrieved_chunks": []},
        None,
        judge,
        _cfg(),
    )

    assert judge.calls == []
    assert result["metrics_used"] == ["rag_retrieval_quality"]


def test_judge_uses_deepeval_metric_classes_without_calling_a_model(monkeypatch, tmp_path):
    import deepeval.metrics as deepeval_metrics

    calls = []

    def fake_metric(metric_name):
        class FakeMetric:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.score = None
                self.reason = ""

            def measure(self, test_case):
                calls.append((metric_name, self.kwargs, test_case))
                self.score = 0.9
                self.reason = "stubbed DeepEval metric"

        return FakeMetric

    for metric_name in ("FaithfulnessMetric", "AnswerRelevancyMetric", "ContextualRelevancyMetric"):
        monkeypatch.setattr(deepeval_metrics, metric_name, fake_metric(metric_name))

    judge = eval_agent.Judge("offline-test-model", tmp_path / "judge-cache.json", enabled=True, retries=1)
    for metric_name in ("faithfulness", "relevancy", "context_relevancy"):
        result = judge.run(
            metric_name,
            "What does the evidence say?",
            "The evidence supports the finding.",
            ["A supporting source excerpt."],
            0.5,
        )
        assert result["score"] == 0.9

    assert {call[0] for call in calls} == {
        "FaithfulnessMetric",
        "AnswerRelevancyMetric",
        "ContextualRelevancyMetric",
    }
    assert all(call[2].retrieval_context == ["A supporting source excerpt."] for call in calls)


def test_orchestration_evaluator_checks_completion_dependencies_and_retries():
    result = eval_agent.eval_orchestration(
        Path("orchestration.json"),
        {
            "status": "completed",
            "plan": {
                "tasks": [
                    {"worker": "internal_intelligence", "goal": "Review internal evidence."},
                    {
                        "worker": "market_intelligence",
                        "goal": "Assess market conditions.",
                        "depends_on": ["internal_intelligence"],
                    },
                ]
            },
            "results": [
                {"worker": "internal_intelligence", "status": "completed"},
                {"worker": "market_intelligence", "status": "completed"},
            ],
            "tool_calls": [{"tool": "internal_search", "duration_ms": 12}],
            "events": [{"name": "task.retry", "duration_ms": 4}],
        },
        None,
        None,
        _cfg(),
    )

    assert result["metrics"]["planned_tasks"] == 2
    assert result["metrics"]["task_completion_pct"] == 100.0
    assert result["metrics"]["missing_dependencies"] == 0
    assert result["metrics"]["tool_calls_observed"] == 1
    assert result["metrics"]["retry_events"] == 1
    assert all(check["passed"] for check in result["checks"])


def test_execution_trace_checks_tool_selection_arguments_sequence_and_retries():
    payload = {
        "execution_trace": {
            "complete": True,
            "allowed_tools": ["web_search", "fetch_page"],
            "expected_sequence": ["web_search", "fetch_page"],
            "calls": [
                {
                    "tool": "web_search",
                    "status": "error",
                    "error_code": "timeout",
                    "retry_of": None,
                    "error_handled": True,
                    "args": {"query": "GitLab Duo roadmap"},
                    "argument_schema": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {"query": {"type": "string"}},
                    },
                },
                {
                    "tool": "fetch_page",
                    "status": "ok",
                    "args": {"url": "https://example.com/duo"},
                    "argument_schema": {
                        "type": "object",
                        "required": ["url"],
                        "properties": {"url": {"type": "string"}},
                    },
                },
            ],
        }
    }

    result = eval_agent.eval_execution_trace(
        payload, "Compare GitLab Duo with competitors.", "A sourced comparison.", RecordingJudge()
    )

    assert result["status"] == "partial"
    assert result["metrics"]["calls"] == 2
    assert result["metrics"]["errors"] == 1
    assert result["dimensions"]["tool_selection"]["status"] == "assessable"
    assert result["dimensions"]["tool_arguments"]["status"] == "assessable"
    assert result["dimensions"]["workflow_sequence"]["status"] == "assessable"
    assert result["dimensions"]["error_handling"]["status"] == "assessable"
    assert result["dimensions"]["tool_result_use"]["status"] == "not_assessable"
    assert all(item["passed"] for item in result["checks"] if item["severity"] == "error")


def test_execution_trace_marks_missing_expectations_and_payload_judgments_unassessable():
    result = eval_agent.eval_execution_trace(
        {"provenance": {"calls": [{"tool": "internal_search", "args": {"query": "strategy"}}]}},
        "Review internal evidence.",
        "The finding uses the search results.",
        RecordingJudge(),
    )

    assert result["status"] == "partial"
    assert result["dimensions"]["tool_selection"]["status"] == "partial"
    assert result["dimensions"]["tool_arguments"]["status"] == "partial"
    assert result["dimensions"]["tool_result_use"]["status"] == "not_assessable"
    assert result["dimensions"]["workflow_sequence"]["status"] == "not_assessable"
    assert result["dimensions"]["efficiency"]["status"] == "assessable"
    assert any(not item["passed"] and item["severity"] == "warn" for item in result["checks"])


def test_execution_trace_schema_detects_missing_and_wrongly_typed_arguments():
    result = eval_agent.eval_execution_trace(
        {
            "execution_trace": {
                "allowed_tools": ["web_search"],
                "calls": [
                    {
                        "tool": "web_search",
                        "args": {"limit": "many"},
                        "argument_schema": {
                            "required": ["query"],
                            "properties": {"limit": {"type": "integer"}},
                        },
                    }
                ],
            }
        },
        "Search for product information.",
        "",
        RecordingJudge(),
    )

    assert result["metrics"]["schema_checks"] == 1
    assert result["metrics"]["schema_errors"] == ["call 1: missing query", "call 1: invalid type for limit"]
    assert result["dimensions"]["tool_arguments"]["status"] == "assessable"


def test_execution_trace_uses_worker_policy_and_privacy_safe_otel_argument_types():
    result = eval_agent.eval_execution_trace(
        {
            "worker": "market_intelligence",
            "spans": [
                {
                    "attributes": {
                        "edrak.tool.name": "market_search",
                        "edrak.tool.argument.names": "queries",
                        "edrak.tool.argument.types": '{"queries":"list"}',
                        "edrak.tool.status": "ok",
                    }
                }
            ],
        },
        "Assess the target market.",
        "",
        RecordingJudge(),
    )

    assert result["dimensions"]["tool_selection"]["status"] == "assessable"
    assert result["dimensions"]["tool_arguments"]["status"] == "assessable"
    assert result["metrics"]["schema_checks"] == 1
    assert result["metrics"]["schema_errors"] == []
    assert next(
        item for item in result["checks"]
        if item["check"] == "tool calls stay within declared/available tools"
    )["passed"]


def test_execution_trace_applies_customer_trends_tool_policy():
    result = eval_agent.eval_execution_trace(
        {
            "worker": "customer_trends",
            "metadata": {
                "provenance": {
                    "calls": [
                        {
                            "tool": "social_search",
                            "branch": "social",
                            "args": {"query": "developer experience"},
                            "status": "ok",
                        }
                    ]
                }
            },
        },
        "Assess customer needs.",
        "",
        RecordingJudge(),
    )

    assert result["dimensions"]["tool_selection"]["status"] == "assessable"
    assert next(
        item for item in result["checks"]
        if item["check"] == "tool calls stay within declared/available tools"
    )["passed"]


def test_execution_trace_rejects_tool_outside_worker_policy_and_false_recovery_claim():
    result = eval_agent.eval_execution_trace(
        {
            "worker": "competitor_intelligence",
            "execution_trace": {
                "calls": [
                    {
                        "tool": "market_search",
                        "status": "error",
                        "error_handled": False,
                    }
                ]
            },
        },
        "Research a competitor.",
        "",
        RecordingJudge(),
    )

    assert result["metrics"]["status"] == "partial"
    assert not next(
        item for item in result["checks"]
        if item["check"] == "tool calls stay within declared/available tools"
    )["passed"]
    failure_check = next(item for item in result["checks"] if item["check"].startswith("tool failures"))
    assert not failure_check["passed"]
    assert "1 unhandled failure(s)" in failure_check["detail"]


def test_execution_efficiency_only_counts_exact_duplicate_calls():
    payload = {
        "execution_trace": {
            "calls": [
                {"tool": "web_search", "args": {"query": "GitLab Duo"}},
                {"tool": "web_search", "args": {"query": "GitHub Copilot"}},
                {"tool": "web_search", "args": {"query": "GitLab Duo"}},
            ]
        }
    }

    result = eval_agent.eval_execution_trace(
        payload, "Compare coding assistants.", "", RecordingJudge()
    )

    assert result["metrics"]["repeated_calls"] == 1
    assert result["dimensions"]["efficiency"]["status"] == "assessable"
    assert any("duplicate tool work" in item["check"] and not item["passed"] for item in result["checks"])


def test_execution_failure_without_recovery_evidence_is_not_passed():
    result = eval_agent.eval_execution_trace(
        {"execution_trace": {"calls": [{"tool": "web_search", "status": "error"}]}},
        "Search for product details.",
        "",
        RecordingJudge(),
    )

    assert result["dimensions"]["error_handling"]["status"] == "partial"
    failure_check = next(item for item in result["checks"] if item["check"].startswith("tool failures"))
    assert not failure_check["passed"]
    assert failure_check["severity"] == "warn"
    assert "1 unhandled failure(s)" in failure_check["detail"]
    assert "web_search=1" in failure_check["detail"]


def test_execution_payload_redacts_secret_fields_and_is_opt_in():
    call = {
        "tool": "web_search",
        "args": {"query": "market strategy", "api_key": "do-not-export"},
        "result_summary": {"summary": "A relevant public filing."},
    }

    safe = eval_agent._execution_summary(call, include_payloads=False)
    opted_in = eval_agent._execution_summary(call, include_payloads=True)

    assert "arguments" not in safe and "result_summary" not in safe
    assert opted_in["arguments"]["query"] == "market strategy"
    assert opted_in["arguments"]["api_key"] == "[REDACTED]"


def test_execution_deepeval_judges_are_dispatched_only_with_required_evidence():
    judge = RecordingJudge()
    judge.enabled = True
    payload = {
        "execution_trace": {
            "complete": True,
            "allowed_tools": ["web_search"],
            "expected_sequence": ["web_search"],
            "calls": [
                {
                    "tool": "web_search",
                    "status": "error",
                    "error_handled": True,
                    "args": {"query": "GitLab Duo"},
                    "result_summary": "Search returned two relevant product pages.",
                }
            ],
        }
    }

    eval_agent.eval_execution_trace(
        payload,
        "Compare GitLab Duo with GitHub Copilot.",
        "GitLab Duo supports ...",
        judge,
        include_payloads=True,
    )

    assert set(judge.questions) == {
        "tool_selection",
        "tool_arguments",
        "tool_result_use",
        "workflow_sequence",
        "error_handling",
        "efficiency",
    }
    assert all(judge.specs[name] == eval_agent.EXECUTION_GEVAL[name] for name in judge.questions)
    serialized_calls = next(call[2] for call in judge.calls if call[0] == "tool_arguments")
    assert "GitLab Duo" in serialized_calls
