import json
from types import SimpleNamespace

import pytest
from edrak.agents.customer_trends import nodes
from edrak.agents.customer_trends.schemas.common import ProcessingResponse, ToolStatus
from edrak.agents.customer_trends.schemas.task import brief_from_task
from edrak.agents.customer_trends.tools import registry
from edrak.agents.customer_trends.tracing import (
    bind_trace_context,
    reset_trace_context,
    save_customer_span,
)
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)
from pydantic import BaseModel


def _task() -> ResearchTask:
    return ResearchTask(
        parent_request_id="request-customer-trends",
        worker=WorkerType.CUSTOMER_TRENDS,
        goal="Understand customer sentiment about AI products.",
        focus="Customer trends and sentiment.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD", "Intel"],
        ),
    )


def test_customer_spanned_nodes_and_tools_are_deepeval_observed():
    for name in (
        "call_tool",
        "plan_queries",
        "social",
        "demand",
        "reviews",
        "analyze",
        "write_findings",
        "submit",
    ):
        assert getattr(getattr(nodes, name), "_is_deepeval_observed", False), name


def test_customer_span_is_saved_under_parent_request_folder(tmp_path):
    task = _task()
    brief = brief_from_task(task)
    artifacts_dir = tmp_path / "configured-artifacts"
    token = bind_trace_context(
        brief,
        request_id=task.parent_request_id,
        artifacts_dir=artifacts_dir,
    )
    try:
        destination = save_customer_span(
            "customer_trends.test",
            input={"query": "sentiment", "events": ["omit"]},
            output={"count": 2},
            metadata={"branch": "social"},
        )
    finally:
        reset_trace_context(token)

    assert destination.resolve() == (
        artifacts_dir
        / "runs"
        / task.parent_request_id
        / "customer_trends_tracing.json"
    )
    record = json.loads(destination.read_text(encoding="utf-8"))
    assert record["request_id"] == task.parent_request_id
    assert record["run_id"] == brief.run_id
    assert record["worker"] == "customer_trends"
    assert record["spans"][0]["input"] == {"query": "sentiment"}
    assert record["spans"][0]["output"] == {"count": 2}
    assert record["spans"][0]["attempt"] == task.attempt


@pytest.mark.asyncio
async def test_node_span_is_saved_under_run_folder(
    monkeypatch,
    tmp_path,
):
    task = _task()
    brief = brief_from_task(task)
    span_updates = []

    async def fake_run_branch(name, _state, _deps):
        assert name == "social"
        return {
            "batches": {"social": ["batch-1"]},
            "branch_notes": {"social": "collected"},
            "branch_errors": {"social": ""},
        }

    monkeypatch.setattr(
        nodes,
        "update_current_span",
        lambda **kwargs: span_updates.append(kwargs),
    )
    monkeypatch.setattr(nodes, "run_branch", fake_run_branch)
    artifacts_dir = tmp_path / "artifacts"
    token = bind_trace_context(
        brief,
        request_id=task.parent_request_id,
        artifacts_dir=artifacts_dir,
    )
    try:
        result = await nodes.social(
            {"brief": brief.model_dump(mode="json")},
            SimpleNamespace(),
        )
    finally:
        reset_trace_context(token)

    assert result["batches"]["social"] == ["batch-1"]
    assert span_updates[0]["input"]["run_id"] == brief.run_id
    assert span_updates[-1]["output"]["batch_ids"] == ["batch-1"]
    trace_path = (
        artifacts_dir
        / "runs"
        / task.parent_request_id
        / "customer_trends_tracing.json"
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    node_span = trace["spans"][-1]
    assert node_span["name"] == "customer_trends.social"
    assert node_span["input"]["run_id"] == brief.run_id
    assert node_span["output"]["batch_ids"] == ["batch-1"]


@pytest.mark.asyncio
async def test_customer_tool_creates_deepeval_and_json_span(monkeypatch, tmp_path):
    task = _task()
    brief = brief_from_task(task)
    updates = []

    class ToolInput(BaseModel):
        query: str

    async def unused_tool(_ctx, _input):
        raise AssertionError("invoke_tool is patched")

    spec = registry.ToolSpec(
        name="test_customer_tool",
        description="Test tool",
        input_model=ToolInput,
        run=unused_tool,
    )
    context = SimpleNamespace(branch="social", run_id=brief.run_id)

    async def fake_invoke(_ctx, _spec, _arguments):
        return ProcessingResponse(status=ToolStatus.OK, count=2)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "invoke_tool", fake_invoke)
    monkeypatch.setattr(
        registry,
        "update_current_span",
        lambda **kwargs: updates.append(kwargs),
    )
    token = bind_trace_context(brief, task.parent_request_id)
    try:
        result = await registry._structured_tool(context, spec).ainvoke(
            {"query": "customer sentiment"}
        )
    finally:
        reset_trace_context(token)

    assert json.loads(result)["count"] == 2
    assert updates[0]["input"] == {"query": "customer sentiment"}
    assert updates[-1]["output"]["status"] == "ok"
    trace_path = (
        tmp_path
        / "artifacts"
        / "runs"
        / task.parent_request_id
        / "customer_trends_tracing.json"
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["spans"][0]["name"] == "customer_trends.test_customer_tool"
    assert trace["spans"][0]["output"]["count"] == 2


def test_every_customer_tool_is_deepeval_observed():
    context = SimpleNamespace(branch="test", run_id="test-run")
    tools = registry.build_tools(context)

    assert set(tools) == {spec.name for spec in registry.ALL_SPECS}
    assert all(
        getattr(tool.coroutine, "_is_deepeval_observed", False)
        for tool in tools.values()
    )