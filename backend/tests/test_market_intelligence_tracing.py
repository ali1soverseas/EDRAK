import json

from edrak.agents.market_intelligence import graph, nodes
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerType,
)


def _task() -> ResearchTask:
    return ResearchTask(
        parent_request_id="market-trace-request",
        worker=WorkerType.MARKET_INTELLIGENCE,
        goal="Assess the AI inference market.",
        focus="Demand and market trends.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD"],
        ),
        attempt=2,
    )


def test_market_trace_is_saved_beside_orchestrator_artifacts(
    monkeypatch, tmp_path
) -> None:
    task = _task()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(nodes, "update_current_span", lambda **_kwargs: None)

    class FakeGraph:
        def invoke(self, state, *, config):
            assert config["configurable"]["thread_id"] == f"market-{task.task_id}"
            traced = nodes.json_trace(
                "test_market_node",
                input_builder=lambda args, _kwargs: {"goal": args[0]["goal"]},
                reset_run=True,
            )(lambda _state: {"captured": "complete node output"})
            traced(state)
            return {
                **state,
                "market_findings": [],
                "market_gaps": ["No market evidence was returned."],
                "final_report": {
                    "gaps": ["No market evidence was returned."],
                    "task_summary": {"skipped": 1},
                },
            }

    monkeypatch.setattr(graph, "get_graph", lambda: FakeGraph())
    graph.run(task)

    trace_path = (
        tmp_path
        / "artifacts"
        / "runs"
        / task.parent_request_id
        / "market_intelligence_tracing.json"
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace[0]["request_id"] == task.parent_request_id
    assert trace[0]["task_id"] == task.task_id
    assert trace[0]["attempt"] == task.attempt
    assert trace[0]["name"] == "test_market_node"
    assert trace[0]["input"] == {"goal": task.goal}
    assert trace[0]["output"] == {"captured": "complete node output"}
