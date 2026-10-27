import json
from pathlib import Path

from edrak.agents import competitor_intelligence
from edrak.agents.competitor_intelligence import nodes
from edrak.agents.competitor_intelligence.state import STAGE_STRATEGY
from edrak.agents.competitor_intelligence.tracing import (
    bind_trace_task,
    reset_trace_task,
    save_competitor_span,
)
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    ResearchTask,
    UseCase,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)


def test_task_to_initial_state_returns_graph_state():
    task = ResearchTask(
        parent_request_id="request-1",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Compare AI accelerators.",
        focus="Products and capabilities.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD", "Intel"],
        ),
    )

    state = competitor_intelligence._task_to_initial_state(task)

    assert isinstance(state, dict)
    assert state["task_id"] == task.task_id
    assert state["competitors"] == ["AMD", "Intel"]


def test_run_research_observes_task_and_result(monkeypatch, tmp_path):
    task = ResearchTask(
        parent_request_id="request-1",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Compare AI accelerators.",
        focus="Products and capabilities.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD", "Intel"],
        ),
    )
    result = WorkerResult(
        task_id=task.task_id,
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        status=WorkerStatus.COMPLETED,
        attempt=task.attempt,
    )
    span_updates = []

    monkeypatch.setattr(
        competitor_intelligence,
        "update_current_span",
        lambda **kwargs: span_updates.append(kwargs),
    )
    monkeypatch.setattr(
        competitor_intelligence,
        "_task_to_initial_state",
        lambda _task: {"competitors": ["AMD", "Intel"]},
    )
    monkeypatch.chdir(tmp_path)

    class FakeGraph:
        def invoke(self, *_args, **_kwargs):
            save_competitor_span(
                "test.graph_node",
                input={"task_id": task.task_id},
                output={"status": "ok"},
            )
            return {}

    monkeypatch.setattr(competitor_intelligence, "app", FakeGraph())
    monkeypatch.setattr(
        competitor_intelligence, "build_worker_result", lambda _state: result
    )

    actual = competitor_intelligence.run_research(task, print_result=False)

    assert actual is result
    assert getattr(competitor_intelligence.run_research, "_is_deepeval_observed", False)
    assert span_updates[0]["input"]["task"]["task_id"] == task.task_id
    assert span_updates[0]["metadata"] == {
        "task_id": task.task_id,
        "worker": "competitor_intelligence",
    }
    assert span_updates[-1]["output"]["status"] == "completed"
    assert span_updates[-1]["output"]["task_id"] == task.task_id
    trace_path = (
        Path("artifacts")
        / "runs"
        / task.parent_request_id
        / "competitor_intelligence_tracing.json"
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["request_id"] == task.parent_request_id
    assert trace["spans"][0]["name"] == "test.graph_node"
    assert trace["spans"][0]["task_id"] == task.task_id


def test_competitor_pipeline_nodes_and_search_tools_are_observed():
    for name in (
        "identify_research_requirements_node",
        "generate_queries_node",
        "search_node",
        "run_tavily",
        "run_serper",
        "verify_node",
        "final_report_node",
    ):
        assert getattr(getattr(nodes, name), "_is_deepeval_observed", False), name


def test_official_search_uses_site_query_domain_if_website_is_unregistered(monkeypatch):
    monkeypatch.setattr(nodes, "official_domains", lambda _competitor: [])

    query, domain_hint = nodes._query_without_site_operator(
        'site:broadcom.com "AI accelerator" customers'
    )

    assert query == '"AI accelerator" customers'
    assert domain_hint == ["broadcom.com"]
    assert nodes._search_domains(
        "Intel Corp",
        "Intel Gaudi pricing",
        "official_sources",
        ["intel.com"],
    ) == ["intel.com"]


def test_discovery_search_does_not_apply_site_query_domain():
    assert nodes._search_domains(
        "Intel Corp",
        "Intel Gaudi pricing",
        "discovery",
        ["intel.com"],
    ) == []


def test_research_stage_order_starts_with_official_sources():
    assert STAGE_STRATEGY == {
        1: "official_sources",
        2: "discovery",
        3: "targeted_missing_requirements",
    }


def test_run_serper_records_search_without_recording_api_key(monkeypatch, tmp_path):
    updates = []
    secret = "serper-test-secret"
    task = ResearchTask(
        parent_request_id="request-serper",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Compare AI accelerators.",
        focus="Products and capabilities.",
        company_profile=CompanyProfile(name="NVIDIA"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["AMD", "Intel"],
        ),
    )
    response_data = {
        "organic": [
            {
                "title": "Company AI products",
                "link": "https://example.com/ai",
                "snippet": "Product details",
                "date": "2026-10-26",
            }
        ]
    }

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return response_data

    monkeypatch.setenv("SERPER_API_KEY", secret)
    monkeypatch.setattr(
        nodes,
        "update_current_span",
        lambda **kwargs: updates.append(kwargs),
    )
    monkeypatch.setattr(
        nodes.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )
    monkeypatch.chdir(tmp_path)

    trace_token = bind_trace_task(task)
    try:
        result, status = nodes.run_serper("company AI products", stage=2)
    finally:
        reset_trace_task(trace_token)

    assert result == response_data
    assert status == 200
    assert updates[0]["input"] == {
        "query": "company AI products",
        "stage": 2,
    }
    assert updates[-1]["output"]["result_count"] == 1
    assert secret not in repr(updates)
    saved_trace = json.loads(
        (
            tmp_path
            / "artifacts"
            / "runs"
            / task.parent_request_id
            / "competitor_intelligence_tracing.json"
        ).read_text(encoding="utf-8")
    )
    assert saved_trace["spans"][0]["name"] == "serper_search"
    assert saved_trace["spans"][0]["output"]["status"] == 200
