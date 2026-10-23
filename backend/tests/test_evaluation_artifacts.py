import json
import inspect

from edrak import observability
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    ResearchPlan,
    ResearchTask,
    RunStatus,
    UseCase,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.contracts.evidence import Evidence, SourceType
from edrak.contracts.result import OrchestrationResult
from edrak.contracts.verification import (
    VerificationDecision,
    VerificationResult,
    VerificationStatus,
)
from edrak.evaluation_artifacts import EvaluationArtifactBundle
from edrak.observability import spans_for_run

from Evaluation.evaluation_Agent import edrak_eval_agent_enhanced as evaluator


def _request() -> BusinessRequest:
    return BusinessRequest(
        request_id="analysis-1",
        goal="Assess the AI coding assistant market.",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub"],
        ),
    )


def _plan(request: BusinessRequest) -> ResearchPlan:
    return ResearchPlan(
        plan_id="plan-1",
        request_id=request.request_id,
        tasks=[
            ResearchTask(
                task_id="task-1",
                parent_request_id=request.request_id,
                worker=WorkerType.MARKET_INTELLIGENCE,
                goal="Research market adoption.",
                focus="Adoption and size",
                company_profile=request.company_profile,
                business_context=request.business_context,
            )
        ],
    )


def test_run_bundle_persists_evaluator_inputs_and_stage_history(tmp_path, monkeypatch):
    monkeypatch.setenv("EDRAK_EVALUATION_ARTIFACTS_DIR", str(tmp_path))
    request = _request()
    plan = _plan(request)
    bundle = EvaluationArtifactBundle(request.request_id, request, plan)
    worker_result = WorkerResult(
        task_id="task-1",
        worker=WorkerType.MARKET_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
    )
    bundle.record_node("dispatch", {"results": [worker_result]}, {"plan": plan})
    verification = VerificationResult(
        research_run_id=request.request_id,
        decision=VerificationDecision(
            status=VerificationStatus.VERIFIED,
            summary="Evidence was reviewed.",
        ),
    )
    bundle.record_node("verify", {"verification_result": verification}, {})
    bundle.save_orchestration_result(
        OrchestrationResult(
            request_id=request.request_id,
            status=RunStatus.COMPLETED,
            plan=plan,
            results=[worker_result],
        )
    )
    bundle.finish("completed")

    input_files = sorted(bundle.inputs.glob("*.json"))
    detected = {
        evaluator.detect(json.loads(path.read_text(encoding="utf-8")))
        for path in input_files
    }
    assert detected == {"input", "worker", "verification", "orchestration"}
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert [stage["node"] for stage in manifest["stages"]] == ["dispatch", "verify"]
    worker_file = next(bundle.inputs.glob("worker_*.json"))
    worker_data = json.loads(worker_file.read_text(encoding="utf-8"))
    assert worker_data["metadata"]["research_goal"] == "Research market adoption."


def test_run_bundle_exports_internal_retrievals_as_rag_evaluator_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("EDRAK_EVALUATION_ARTIFACTS_DIR", str(tmp_path))
    request = _request()
    plan = _plan(request)
    bundle = EvaluationArtifactBundle(request.request_id, request, plan)
    worker_result = WorkerResult(
        task_id="task-1",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        evidence=[
            Evidence(
                source_type=SourceType.INTERNAL_DOCUMENT,
                extracted_fact="The plan prioritized enterprise adoption.",
                excerpt="The plan prioritized enterprise adoption in the next quarter.",
                source_url="internal://strategy",
                metadata={
                    "chunk_id": "chunk-1",
                    "search_query": "What did the internal plan prioritize?",
                    "relevance_score": 0.82,
                },
            )
        ],
    )

    bundle.record_node("dispatch", {"results": [worker_result]}, {"plan": plan})

    rag_path = next(bundle.inputs.glob("rag_*.json"))
    rag_data = json.loads(rag_path.read_text(encoding="utf-8"))
    assert evaluator.detect(rag_data) == "rag"
    assert rag_data["query"] == "What did the internal plan prioritize?"
    assert rag_data["retrieved_chunks"][0]["chunk_id"] == "chunk-1"
    assert rag_data["retrieved_chunks"][0]["score"] == 0.82


def test_run_bundle_saves_metrics_stage_for_evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("EDRAK_EVALUATION_ARTIFACTS_DIR", str(tmp_path))
    request = _request()
    bundle = EvaluationArtifactBundle(request.request_id, request, _plan(request))
    worker_result = WorkerResult(
        task_id="task-1",
        worker=WorkerType.MARKET_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        metadata={"metrics": [], "requirements": [], "requirement_outcomes": []},
    )

    bundle.record_node("metrics", {"results": [worker_result]}, {"results": [worker_result]})

    metrics_path = next(bundle.outputs.glob("metrics_*.json"))
    metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics_data["metrics_stage"] is True
    assert evaluator.detect(metrics_data) == "metrics"
    assert metrics_data["results"][0]["metadata"]["requirement_outcomes"] == []


def test_spans_for_run_filters_to_run_tagged_trace(tmp_path, monkeypatch):
    telemetry_path = tmp_path / "spans.json"
    telemetry_path.write_text(
        json.dumps(
            {
                "spans": [
                    {
                        "trace_id": "trace-one",
                        "span_id": "root-one",
                        "attributes": {"edrak.run.id": "analysis-1"},
                    },
                    {"trace_id": "trace-one", "span_id": "tool-one", "attributes": {}},
                    {
                        "trace_id": "trace-two",
                        "span_id": "root-two",
                        "attributes": {"edrak.run.id": "analysis-2"},
                    },
                    {
                        "trace_id": "trace-three",
                        "span_id": "worker-one",
                        "attributes": {"edrak.run.id": "analysis-1"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EDRAK_TELEMETRY_EXPORT_PATH", str(telemetry_path))
    monkeypatch.setattr(observability, "flush_telemetry", lambda: None)

    spans = spans_for_run("analysis-1", "trace-one")

    assert [span["span_id"] for span in spans] == ["root-one", "tool-one", "worker-one"]


def test_worker_operation_spans_include_parent_run_id():
    request = _request()
    task = _plan(request).tasks[0]

    class RecordingSpan:
        attributes = {}

        def set_attribute(self, key, value):
            self.attributes[key] = value

    span = RecordingSpan()
    observability._set_operation_attributes(
        span,
        inspect.signature(lambda task: None),
        (task,),
        {},
    )

    assert span.attributes["edrak.run.id"] == request.request_id


def test_run_bundle_keeps_spans_across_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("EDRAK_EVALUATION_ARTIFACTS_DIR", str(tmp_path))
    request = _request()
    bundle = EvaluationArtifactBundle(request.request_id, request, _plan(request))

    bundle.save_telemetry([{"trace_id": "trace-one", "span_id": "span-one"}])
    bundle.save_telemetry([{"trace_id": "trace-two", "span_id": "span-two"}])

    payload = json.loads((bundle.inputs / "telemetry.json").read_text(encoding="utf-8"))
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert {span["trace_id"] for span in payload["spans"]} == {"trace-one", "trace-two"}
    assert manifest["telemetry_spans"] == 2
