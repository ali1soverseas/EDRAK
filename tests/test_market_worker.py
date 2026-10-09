import json

from edrak.agents.market_intelligence.graph import (
    build_graph,
    run,
    task_router,
    worker_result_from_state,
)
from edrak.agents.market_intelligence.nodes import output_node, task_planner
from edrak.agents.market_intelligence.schemas import (
    AnalysisClaim,
    PlannedTask,
    SearchQuery,
)
from edrak.agents.market_intelligence.state import empty_market_state
from edrak.contracts import SourceType, WorkerResult, WorkerStatus, WorkerType


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


def test_task_planner_falls_back_when_llm_returns_invalid_json(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    # call_structured is the seam now: it returns None when the reply does not
    # fit the schema, which is the failure this test is about.
    monkeypatch.setattr(nodes, "call_structured", lambda *_a, **_k: None)
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


SCRAPE_TEXT = (
    "Enterprise teams are adopting AI coding assistants across the software "
    "lifecycle. A 2025 survey of 400 DevOps organisations found 62% now run at "
    "least one AI coding assistant in production."
)


def test_run_returns_worker_result_with_unchanged_flow(monkeypatch, sample_research_task):
    from edrak.agents.market_intelligence import nodes

    def fake_structured(schema, _prompt, **_kwargs):
        """The bound-call seam. Returns schema instances, not JSON text."""
        if schema is nodes.TaskPlan:
            return nodes.TaskPlan(
                tasks=[
                    PlannedTask(
                        description="Identify current market size of AI coding assistants",
                        tool_hint="tool_serper",
                    )
                ]
            )
        if schema is nodes.QueryArgs:
            return nodes.QueryArgs(
                queries=[SearchQuery(q="AI coding assistants market", language="en")]
            )
        if schema is nodes.Usefulness:
            return nodes.Usefulness(useful=True)
        if schema is nodes.AnalysisReply:
            return nodes.AnalysisReply(
                claims=[
                    AnalysisClaim(
                        # No capitalised proper noun: a name in the claim that also
                        # appears in the quote adds 0.15, which would put this at
                        # 0.9 rather than the 0.75 asserted below.
                        claim="A 2025 survey found AI coding assistant adoption at 62%.",
                        # Copied verbatim from the scrape below. claim_is_supported
                        # needs the quote inside the source and at least 40
                        # characters, and claim_confidence needs a digit in the
                        # claim plus a quote of 100 or more characters for 0.75.
                        # A short or paraphrased quote fails both.
                        quote=SCRAPE_TEXT,
                    )
                ]
            )
        return None

    monkeypatch.setattr(nodes, "call_structured", fake_structured)
    monkeypatch.setitem(nodes.TOOL_MAP, "tool_serper", _FakeTool())
    monkeypatch.setattr(nodes, "scrape_url_content", lambda _url: SCRAPE_TEXT)

    result = run(sample_research_task)

    assert isinstance(result, WorkerResult)
    assert result.worker is WorkerType.MARKET_INTELLIGENCE
    assert result.task_id == sample_research_task.task_id
    assert result.status is WorkerStatus.COMPLETED
    assert result.findings
    assert result.evidence
    assert result.findings[0].evidence_refs
    assert result.evidence[0].source_type is SourceType.WEB_PAGE
    assert result.evidence[0].source_url == "https://example.com/ai-devops"
    assert result.confidence == 0.75
    assert result.findings[0].confidence == 0.75
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.completed_at >= result.started_at
    assert result.error == "none"


def test_worker_result_from_state_marks_partial_when_tasks_skipped(sample_research_task):
    state = empty_market_state(sample_research_task.parent_request_id, "goal", "")
    state["market_findings"] = [
        {"task": "A", "claim": "One finding", "evidence": [{"type": "endpoint", "source": "[tool_serper]"}]}
    ]
    state["final_report"] = {"task_summary": {"done": 1, "skipped": 1, "total": 2}}
    result = worker_result_from_state(sample_research_task, state)
    assert result.status is WorkerStatus.PARTIAL
    assert result.evidence[0].source_type is SourceType.OTHER
    assert result.findings[0].statement == "One finding"
    # 0.35, not 0.55. The finding carries no confidence because this path takes
    # private evidence items rather than analysed claims, and the fallback for a
    # missing value is deliberately low. The test previously asserted 0.55,
    # which is claim_confidence's answer for a pair it never passes: the code
    # under test does not call claim_confidence at all here.
    assert result.confidence == 0.35
    assert result.findings[0].confidence == 0.35
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.error == "Some research tasks were skipped."
