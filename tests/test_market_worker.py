import json

from edrak.contracts import ResearchTask, WorkerResult
from edrak.workers.market.graph import (
    build_graph,
    run,
    task_router,
    worker_result_from_state,
)
from edrak.workers.market.nodes import clean_json, output_node, task_planner
from edrak.workers.market.state import empty_market_state


class _FakeTool:
    name = "tool_serper"
    description = "Google Search via the Serper API"

    def invoke(self, _args):
        return json.dumps([
            {
                "organic": [
                    {
                        "link": "https://example.com/ai-devops",
                        "title": "AI DevOps market",
                        "snippet": "Enterprise adoption of AI coding assistants continues to grow across DevOps platforms.",
                    }
                ],
                "query_used": "AI DevOps",
                "method": "serper",
            }
        ])


def test_graph_keeps_planner_executor_output_loop():
    app = build_graph()
    node_ids = set(app.get_graph().nodes)
    assert {"task_planner", "task_executor", "output_node"} <= node_ids


def test_task_router_loops_until_tasks_are_consumed():
    more_work = empty_market_state("r1", "goal", "context")
    more_work["task_list"] = [
        {"id": 1, "description": "Market size", "tool_hint": "tool_serper", "status": "pending"}
    ]
    assert task_router(more_work) == "task_executor"

    done = dict(more_work)
    done["current_task_idx"] = 1
    assert task_router(done) == "output_node"


def test_clean_json_strips_fences():
    raw = '```json\n{"useful": true}\n```'
    assert json.loads(clean_json(raw)) == {"useful": True}


def test_task_planner_falls_back_when_llm_returns_invalid_json(monkeypatch):
    from edrak.workers.market import nodes

    monkeypatch.setattr(nodes, "call_llm", lambda _prompt: "not-json")
    state = empty_market_state("r1", "Understand AI DevOps demand", "GitLab context")
    update = task_planner(state)

    assert update["current_task_idx"] == 0
    assert update["task_list"][0]["status"] == "pending"
    assert "Understand AI DevOps demand" in update["task_list"][0]["description"]


def test_output_node_builds_private_report_without_raw_docs():
    state = empty_market_state("r1", "goal", "context")
    state["task_list"] = [
        {"id": 1, "description": "Market size", "tool_hint": "tool_serper", "status": "done"}
    ]
    state["market_findings"] = [
        {
            "task": "Market size",
            "claim": "The market is expanding.",
            "evidence": [{"type": "url", "source": "https://example.com"}],
        }
    ]
    update = output_node(state)
    report = update["final_report"]
    assert report["task_summary"]["done"] == 1
    assert "raw" not in report
    assert report["market_findings"][0]["claim"] == "The market is expanding."


def test_run_returns_worker_result_with_unchanged_flow(monkeypatch):
    from edrak.workers.market import nodes

    def fake_llm(prompt: str) -> str:
        if "planning agent" in prompt:
            return json.dumps({
                "tasks": [
                    {
                        "id": 1,
                        "description": "Identify current market size of AI coding assistants",
                        "tool_hint": "tool_serper",
                    }
                ]
            })
        if "generating search arguments" in prompt:
            return json.dumps({"queries": ["AI coding assistants market"]})
        if "USEFUL for the research task" in prompt:
            return json.dumps({"useful": True})
        if "market research analyst" in prompt:
            return json.dumps({
                "claim": "Adoption of AI coding assistants is increasing among enterprise DevOps teams."
            })
        return "{}"

    monkeypatch.setattr(nodes, "call_llm", fake_llm)
    monkeypatch.setitem(nodes.TOOL_MAP, "tool_serper", _FakeTool())
    monkeypatch.setattr(
        nodes,
        "scrape_url_content",
        lambda _url: "Enterprise teams are adopting AI coding assistants across the software lifecycle. " * 4,
    )

    task = ResearchTask(
        task_id="task-1",
        run_id="run-1",
        worker="market",
        goal="Understand the AI coding assistant market",
        business_context="GitLab vs GitHub Copilot",
    )
    result = run(task)

    assert isinstance(result, WorkerResult)
    assert result.worker == "market"
    assert result.task_id == "task-1"
    assert result.status == "success"
    assert result.findings
    assert result.findings[0].evidence[0].type == "url"
    assert result.findings[0].evidence[0].source == "https://example.com/ai-devops"


def test_worker_result_from_state_marks_partial_when_tasks_skipped():
    task = ResearchTask(
        task_id="task-1",
        run_id="run-1",
        worker="market",
        goal="goal",
    )
    state = empty_market_state("run-1", "goal", "")
    state["market_findings"] = [
        {"task": "A", "claim": "One finding", "evidence": [{"type": "endpoint", "source": "[tool_serper]"}]}
    ]
    state["final_report"] = {"task_summary": {"done": 1, "skipped": 1, "total": 2}}
    result = worker_result_from_state(task, state)
    assert result.status == "partial"
