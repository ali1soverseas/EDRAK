from pathlib import Path
from unittest.mock import Mock

from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink


def test_customer_result_uses_parent_request_folder_and_keeps_attempts_separate(
    tmp_path: Path,
) -> None:
    sink = LocalSink(
        Mock(spec=EvidenceStore),
        tmp_path,
        parent_request_id="parent-request",
    )

    first = sink.result_path("task-id.a1")
    retry = sink.result_path("task-id.a2")

    assert first == (
        tmp_path / "runs" / "parent-request" / "customer_trends_task-id.a1_result.json"
    )
    assert retry == (
        tmp_path / "runs" / "parent-request" / "customer_trends_task-id.a2_result.json"
    )
