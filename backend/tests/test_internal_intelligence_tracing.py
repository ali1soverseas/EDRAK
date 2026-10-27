import json
from pathlib import Path

from edrak.agents.internal_intelligence import tracing
from edrak.agents.internal_intelligence.tracing import (
    bind_trace_context,
    reset_trace_context,
    save_internal_span,
)
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)


def test_internal_trace_is_saved_beside_orchestrator_artifacts(tmp_path) -> None:
    task = ResearchTask(
        parent_request_id="internal-trace-request",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Assess internal product strategy.",
        focus="Product strategy and pricing.",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["GitHub"],
        ),
        attempt=2,
    )
    token = bind_trace_context(task, artifacts_dir=tmp_path)
    try:
        destination = save_internal_span(
            "internal_test_node",
            input={"goal": task.goal},
            output={"finding_count": 1},
        )
    finally:
        reset_trace_context(token)

    assert destination == (
        tmp_path
        / "runs"
        / task.parent_request_id
        / "internal_intelligence_tracing.json"
    )
    trace = json.loads(destination.read_text(encoding="utf-8"))
    assert trace["request_id"] == task.parent_request_id
    assert trace["run_id"] == f"{task.task_id}.a2"
    assert trace["spans"][0]["task_id"] == task.task_id
    assert trace["spans"][0]["attempt"] == 2


def test_internal_trace_default_root_is_independent_of_backend_working_directory(
    monkeypatch, tmp_path
) -> None:
    task = ResearchTask(
        parent_request_id="internal-trace-default-root",
        worker=WorkerType.INTERNAL_INTELLIGENCE,
        goal="Assess internal product strategy.",
        focus="Product strategy and pricing.",
        company_profile=CompanyProfile(name="GitLab"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    monkeypatch.chdir(backend_dir)
    monkeypatch.setattr(tracing.settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(tracing.settings, "ARTIFACTS_PATH", Path("artifacts"))

    token = bind_trace_context(task)
    try:
        destination = save_internal_span(
            "internal_test_node",
            input={"goal": task.goal},
            output={"finding_count": 1},
        )
    finally:
        reset_trace_context(token)

    assert destination == (
        tmp_path
        / "artifacts"
        / "runs"
        / task.parent_request_id
        / "internal_intelligence_tracing.json"
    )
    assert not (backend_dir / "artifacts").exists()
