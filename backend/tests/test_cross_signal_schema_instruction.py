from __future__ import annotations

import asyncio

from edrak.CrossSignal.nodes import CrossSignalNodes
from edrak.CrossSignal.graph import build_cross_signal_graph
from edrak.CrossSignal.state import CrossSignalSettings, SignalDetectionResult


def test_detection_includes_the_structured_schema_for_each_lens(monkeypatch) -> None:
    class FakeStructuredModel:
        def __init__(self) -> None:
            self.messages = []

        async def ainvoke(self, messages):
            self.messages.append(messages)
            return SignalDetectionResult(signals=[])

    class FakeModel:
        def __init__(self) -> None:
            self.structured = FakeStructuredModel()

        def with_structured_output(self, _schema, **_kwargs):
            return self.structured

    model = FakeModel()
    monkeypatch.setattr(
        "edrak.CrossSignal.nodes.trace_cross_signal",
        lambda *_args, **_kwargs: None,
    )
    node = CrossSignalNodes(
        llm=model,  # type: ignore[arg-type]
        settings=CrossSignalSettings(),
    )

    result = asyncio.run(node.detect_signals({
        "findings_digest": "verified evidence",
        "business_digest": "competitive intelligence",
    }))

    assert result["candidate_signals"] == []
    assert len(model.structured.messages) == 3
    for messages in model.structured.messages:
        schema_message = messages[-1].content
        assert "JSON schema" in schema_message
        assert '"signals"' in schema_message


def test_cross_signal_uses_configured_model_and_long_form_output_budget(monkeypatch) -> None:
    calls = {}

    def fake_get_chat_model(model, **kwargs):
        calls["model"] = model
        calls.update(kwargs)
        return object()

    monkeypatch.setattr("edrak.CrossSignal.graph.get_chat_model", fake_get_chat_model)

    build_cross_signal_graph(settings=CrossSignalSettings())

    assert calls["model"] is None
    assert calls["agent"] == "cross_signal"
    assert calls["num_predict"] == 32768
