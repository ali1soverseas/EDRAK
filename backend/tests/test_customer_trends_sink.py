import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from edrak.agents.customer_trends.schemas.findings import (
    Confidence,
    ControlSummary,
    CustomerTrendsResult,
    EvidenceRef,
)
from edrak.agents.customer_trends.schemas.task import brief_from_task
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)


def _result(attempt: int) -> CustomerTrendsResult:
    task = ResearchTask(
        parent_request_id="parent-request",
        task_id="task-id",
        worker=WorkerType.CUSTOMER_TRENDS,
        goal="Understand customer sentiment about AI products.",
        focus="Customer trends and sentiment.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD", "Intel"],
        ),
        attempt=attempt,
    )
    brief = brief_from_task(task)
    return CustomerTrendsResult(
        task_id=brief.task_id,
        run_id=brief.run_id,
        brief=brief,
        evidence_ref=EvidenceRef(store_path="data/evidence.db", run_id=brief.run_id, count=0),
        control_summary=ControlSummary(
            status="insufficient",
            headline="",
            overall_confidence=Confidence.LOW,
            findings_count=0,
            evidence_count=0,
        ),
        created_at=datetime.now(UTC),
    )


def test_customer_results_share_one_trace_artifact_and_keep_attempts_readable(
    tmp_path: Path,
) -> None:
    store = Mock(spec=EvidenceStore)
    sink = LocalSink(
        store,
        tmp_path,
        parent_request_id="parent-request",
    )

    first_result = _result(1)
    retry_result = _result(2)
    first = Path(sink.write_result(first_result))
    retry = Path(sink.write_result(retry_result))
    trace_path = tmp_path / "runs" / "parent-request" / "customer_trends_tracing.json"

    assert first == retry == trace_path
    assert [path.name for path in trace_path.parent.iterdir()] == ["customer_trends_tracing.json"]
    record = json.loads(trace_path.read_text(encoding="utf-8"))
    assert set(record["results"]) == {first_result.run_id, retry_result.run_id}
    assert sink.read_result(first_result.run_id) == first_result
    assert sink.read_result(retry_result.run_id) == retry_result
    assert store.register_result.call_count == 2
