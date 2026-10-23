import asyncio
import json
from types import SimpleNamespace
from typing import Any

from langchain_core.tools import StructuredTool
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from edrak.agents.customer_trends.schemas.common import ToolResponse, ToolStatus
from edrak.agents.customer_trends.tools import registry
from edrak.agents.customer_trends.tools.base import ToolSpec
from edrak.observability import trace


class SearchInput(BaseModel):
    query: str
    api_token: str


def test_customer_trends_tool_calls_are_traced_with_worker_context(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)

    async def fake_invoke_tool(_ctx: Any, _spec: ToolSpec, arguments: dict[str, Any]) -> ToolResponse:
        assert arguments["query"] == "NVIDIA customer demand"
        return ToolResponse(
            status=ToolStatus.OK,
            provider_used="serper",
            count=2,
        )

    monkeypatch.setattr(registry, "invoke_tool", fake_invoke_tool)
    spec = ToolSpec("web_search", "Search the web.", SearchInput, fake_invoke_tool)
    tool = registry._structured_tool(SimpleNamespace(task_id="customer-task"), spec)
    assert isinstance(tool, StructuredTool)

    async def invoke() -> None:
        tracer = provider.get_tracer("test.customer_trends")
        with tracer.start_as_current_span("edrak.worker.run") as worker_span:
            worker_span.set_attribute("edrak.run.id", "analysis-run")
            worker_span.set_attribute("edrak.agent.type", "customer_trends")
            worker_span.set_attribute("edrak.task.id", "customer-task")
            worker_span.set_attribute("edrak.task.attempt", 1)
            await tool.ainvoke({
                "query": "NVIDIA customer demand",
                "api_token": "must-not-appear",
            })

    asyncio.run(invoke())
    tool_span = next(span for span in exporter.get_finished_spans() if span.name == "tool.call")
    attributes = tool_span.attributes
    assert attributes["edrak.tool.name"] == "web_search"
    assert attributes["edrak.tool.provider"] == "serper"
    assert attributes["edrak.tool.status"] == "ok"
    assert attributes["edrak.tool.argument.values"] == json.dumps({
        "api_token": "[REDACTED]",
        "query": "NVIDIA customer demand",
    }, separators=(",", ":"), sort_keys=True)
    assert tool_span.parent is not None
    parent = next(
        span for span in exporter.get_finished_spans()
        if span.context.span_id == tool_span.parent.span_id
    )
    assert parent.attributes["edrak.agent.type"] == "customer_trends"
    assert parent.attributes["edrak.task.id"] == "customer-task"
