import asyncio
import json
from contextlib import contextmanager

import pytest

from edrak.observability import _set_tool_result, traced_tool


class RecordingSpan:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, name, value):
        self.attributes[name] = value


def test_cancelled_tool_call_is_not_recorded_as_success(monkeypatch):
    span = RecordingSpan()

    class RecordingTracer:
        @contextmanager
        def start_as_current_span(self, _name):
            yield span

    monkeypatch.setattr("edrak.observability.trace.get_tracer", lambda _name: RecordingTracer())

    @traced_tool("slow_tool")
    async def slow_tool():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(slow_tool())

    assert span.attributes["edrak.tool.status"] == "error"
    assert span.attributes["edrak.tool.error_code"] == "CancelledError"


def test_tool_result_payload_is_captured_and_redacted_by_default(monkeypatch):
    monkeypatch.delenv("EDRAK_TELEMETRY_INCLUDE_TOOL_PAYLOADS", raising=False)
    span = RecordingSpan()

    _set_tool_result(span, '[{"excerpt":"result","api_key":"secret"}]')

    assert span.attributes["edrak.tool.result.count"] == 1
    assert json.loads(span.attributes["edrak.tool.result.summary"]) == {
        "count": 1,
        "decoded_json": True,
        "item_keys": ["excerpt"],
        "item_types": ["dict"],
        "type": "array",
    }
    assert json.loads(span.attributes["edrak.tool.result.payload"]) == [
        {"excerpt": "result", "api_key": "[REDACTED]"}
    ]


def test_tool_result_payload_capture_can_be_disabled(monkeypatch):
    monkeypatch.setenv("EDRAK_TELEMETRY_INCLUDE_TOOL_PAYLOADS", "false")
    span = RecordingSpan()

    _set_tool_result(span, {"excerpt": "result"})

    assert "edrak.tool.result.payload" not in span.attributes


def test_tool_result_payload_keeps_redacted_data_below_capture_limit(monkeypatch):
    monkeypatch.delenv("EDRAK_TELEMETRY_INCLUDE_TOOL_PAYLOADS", raising=False)
    span = RecordingSpan()
    result = {f"field_{index}": "x" * 900 for index in range(6)}

    _set_tool_result(span, result)

    payload = json.loads(span.attributes["edrak.tool.result.payload"])
    assert payload == result


def test_tool_result_summary_counts_nested_result_collections():
    span = RecordingSpan()

    _set_tool_result(span, {
        "results": [{"title": "one"}, {"title": "two"}],
        "api_key": "secret",
    })

    assert json.loads(span.attributes["edrak.tool.result.summary"]) == {
        "collection_counts": {"results": 2},
        "decoded_json": False,
        "field_count": 2,
        "keys": ["results"],
        "type": "object",
    }
