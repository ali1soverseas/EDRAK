import json

from edrak.observability import _set_tool_result


class RecordingSpan:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, name, value):
        self.attributes[name] = value


def test_tool_result_summary_counts_json_encoded_arrays_without_payload_capture(monkeypatch):
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
    assert "edrak.tool.result.payload" not in span.attributes


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
