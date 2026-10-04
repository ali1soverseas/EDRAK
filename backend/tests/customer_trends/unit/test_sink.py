import json
from pathlib import Path

import pytest

from edrak.agents.customer_trends.schemas.common import Confidence, FindingType
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters
from edrak.agents.customer_trends.schemas.findings import Finding
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.store.sink import LocalSink, ResultSink
from tests.customer_trends.factories import RUN_ID, TASK_ID, load_brief, make_evidence, make_result

ARABIC_TEXT = "مَرْحَبًا بِكُمْ: الدعم الفني بطيء جدا ـــ والسعر مرتفع (٣٠٪) 🔥 and GitLab Duo"


@pytest.fixture
def sink(store: EvidenceStore, tmp_path: Path) -> LocalSink:
    return LocalSink(store, tmp_path / "artifacts")


def test_local_sink_satisfies_the_protocol(sink: LocalSink) -> None:
    protocol_user: ResultSink = sink
    assert protocol_user is sink


def test_write_and_read_back(sink: LocalSink, store: EvidenceStore, tmp_path: Path) -> None:
    result = make_result()
    store.create_run(result.run_id, result.task_id)
    location = sink.write_result(result)
    expected = tmp_path / "artifacts" / "runs" / result.run_id / "customer_trends" / "result.json"
    assert location == str(expected)
    assert expected.is_file()
    assert sink.read_result(result.run_id) == result
    assert store.result_location(result.run_id) == location


def test_result_file_is_pretty_utf8_json_without_escaped_unicode(
    sink: LocalSink, store: EvidenceStore
) -> None:
    result = make_result(headline="ملخص النتائج")
    store.create_run(result.run_id, result.task_id)
    path = Path(sink.write_result(result))
    raw = path.read_bytes()
    assert b"\n  " in raw
    assert "ملخص النتائج".encode() in raw
    assert b"\\u06" not in raw
    assert json.loads(raw)["schema_version"] == "1.0"


def test_rewriting_replaces_the_previous_result(sink: LocalSink, store: EvidenceStore) -> None:
    first = make_result(headline="first")
    store.create_run(first.run_id, first.task_id)
    sink.write_result(first)
    sink.write_result(make_result(headline="second"))
    assert sink.read_result(first.run_id).control_summary.headline == "second"
    assert list(Path(sink.result_path(first.run_id)).parent.iterdir()) == [
        sink.result_path(first.run_id)
    ]


def test_reading_a_missing_result_fails_clearly(sink: LocalSink) -> None:
    with pytest.raises(FileNotFoundError):
        sink.read_result("run-missing")


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "", ".hidden", "x" * 200])
def test_unsafe_run_ids_are_refused(sink: LocalSink, run_id: str) -> None:
    with pytest.raises(ValueError, match="invalid run_id"):
        sink.read_result(run_id)


def test_arabic_evidence_survives_store_query_sink_write_and_sink_read_byte_for_byte(
    store: EvidenceStore, sink: LocalSink
) -> None:
    brief = load_brief()
    store.create_run(RUN_ID, TASK_ID, brief)
    original = make_evidence(ARABIC_TEXT, language="ar", metadata={"note": ARABIC_TEXT})
    store.add_batch(RUN_ID, TASK_ID, "social", [original])

    queried = store.query(RUN_ID, EvidenceFilters(language="ar")).items
    assert [item.text.encode("utf-8") for item in queried] == [ARABIC_TEXT.encode("utf-8")]
    assert queried[0].metadata["note"].encode("utf-8") == ARABIC_TEXT.encode("utf-8")

    result = make_result(
        brief.model_copy(update={"run_id": RUN_ID, "task_id": TASK_ID}),
        findings=[
            Finding(
                id="f-ar",
                type=FindingType.SENTIMENT,
                claim=f"Users write: {queried[0].text}",
                confidence=Confidence.LOW,
                evidence_ids=[queried[0].id],
            )
        ],
        provenance={"quote": queried[0].text},
    )
    path = Path(sink.write_result(result))
    assert ARABIC_TEXT.encode("utf-8") in path.read_bytes()

    again = sink.read_result(RUN_ID)
    assert again.provenance["quote"].encode("utf-8") == ARABIC_TEXT.encode("utf-8")
    assert again.findings[0].claim == f"Users write: {ARABIC_TEXT}"
    assert again == result
