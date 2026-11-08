import json

from edrak.agents.market_intelligence.graph import (
    build_graph,
    publisher_from_url,
    run,
    task_router,
    worker_result_from_state,
)
from edrak.agents.market_intelligence.nodes import (
    adoption_stage_gap,
    claim_is_supported,
    conclude,
    fill_gaps,
    output_node,
    stand_in_limitation,
    task_executor,
    task_planner,
)
from edrak.agents.market_intelligence.prompts import task_planner_prompt
from edrak.agents.market_intelligence.schemas import (
    AnalysisClaim,
    PlannedTask,
    SearchQuery,
)
from edrak.agents.market_intelligence.search_scope import market_label, markets_from_brief
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
    assert {"task_planner", "task_executor", "conclude", "output_node"} <= node_ids


def test_task_router_loops_until_tasks_are_consumed():
    more_work = empty_market_state("r1", "goal", "context")
    more_work["task_list"] = [
        {"id": 1, "description": "Market size", "tool_hint": "tool_serper", "status": "pending"}
    ]
    assert task_router(more_work) == "task_executor"

    done = dict(more_work)
    done["current_task_idx"] = 1
    assert task_router(done) == "conclude"


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


US_ACCELERATOR_HIT = (
    "US cloud providers are buying accelerators for new data center clusters. "
    "AWS and Azure announced fresh capacity for AI training this quarter."
)


class _UsAcceleratorTool:
    name = "tool_serper"
    description = "Google Search via the Serper API"

    def invoke(self, _args):
        assert "united states" not in US_ACCELERATOR_HIT.lower()
        return json.dumps([
            {
                "organic": [
                    {
                        "link": "https://example.com/us-accelerators",
                        "title": "US accelerators",
                        "snippet": US_ACCELERATOR_HIT,
                    }
                ],
                "query_used": "US accelerators",
                "method": "serper",
            }
        ])


def _accelerator_task_state() -> dict:
    state = empty_market_state(
        "r1",
        "What is the market momentum for AI accelerators in the United States?",
        "Company: NVIDIA Corporation\nMarkets: United States",
    )
    state["task_list"] = [
        {
            "id": 1,
            "description": "Identify adoption of AI accelerators in the United States",
            "tool_hint": "tool_serper",
            "status": "pending",
            "queries": [{"q": "US accelerators", "language": "en"}],
            "search": {
                "gl": "us",
                "language": "en",
                "hl": "en",
                "place": "united states",
                "recency": "year",
            },
        }
    ]
    return state


def test_relevance_follows_the_model_when_the_place_is_abbreviated(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    assert "united states" not in US_ACCELERATOR_HIT.lower()
    assert "US" in US_ACCELERATOR_HIT and "accelerators" in US_ACCELERATOR_HIT
    monkeypatch.setattr(nodes, "MAX_RETRIES", 1)
    monkeypatch.setattr(nodes, "MAX_TOOL_FALLBACK", 1)
    monkeypatch.setitem(nodes.TOOL_MAP, "tool_serper", _UsAcceleratorTool())
    monkeypatch.setattr(nodes, "scrape_url_content", lambda _url: US_ACCELERATOR_HIT)

    def useful(schema, prompt, **_kwargs):
        if schema is nodes.PageVerdicts:
            assert US_ACCELERATOR_HIT[:40] in prompt
            return nodes.PageVerdicts(
                pages=[{"index": 1, "useful": True, "reason": "about US accelerators"}]
            )
        if schema is nodes.AnalysisReply:
            return nodes.AnalysisReply(
                claims=[
                    AnalysisClaim(
                        claim=US_ACCELERATOR_HIT,
                        quote=US_ACCELERATOR_HIT,
                    )
                ]
            )
        return None

    monkeypatch.setattr(nodes, "call_structured", useful)
    kept = task_executor(_accelerator_task_state())
    assert kept["task_list"][0]["status"] == "done"
    assert kept["market_findings"][0]["claim"] == US_ACCELERATOR_HIT
    assert "united states" not in kept["market_findings"][0]["quote"].lower()

    def rejected(schema, prompt, **_kwargs):
        assert schema is nodes.PageVerdicts
        assert US_ACCELERATOR_HIT[:40] in prompt
        return nodes.PageVerdicts(
            pages=[{"index": 1, "useful": False, "reason": "different industry"}]
        )

    monkeypatch.setattr(nodes, "call_structured", rejected)
    dropped = task_executor(_accelerator_task_state())
    assert dropped["task_list"][0]["status"] == "skipped"
    assert dropped["market_gaps"]
    assert "market_findings" not in dropped


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
        if schema is nodes.PageVerdicts:
            return nodes.PageVerdicts(pages=[{"index": 1, "useful": True}])
        if schema is nodes.AnalysisReply:
            return nodes.AnalysisReply(
                claims=[
                    AnalysisClaim(
                        # No capitalised proper noun: a name in the claim that also
                        # appears in the quote adds 0.15, which would put this at
                        # 0.9 rather than the 0.75 asserted below.
                        claim=(
                            "A 2025 survey found 62% now run at least one AI "
                            "coding assistant in production."
                        ),
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
    assert result.error == ""


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
    assert result.error == ""


def test_claim_gate_rejects_words_and_figures_the_quote_does_not_state():
    residency = (
        "Organisations must keep personal data stored in specific geographic "
        "locations under the residency rule described here."
    )
    accelerator = (
        "The residency rule compels accelerator deployments in data centers "
        "across the region described here."
    )
    assert not claim_is_supported(accelerator, residency, residency)

    market = (
        "The overall market is poised to grow at 29.77% CAGR through 2031 "
        "according to the published forecast."
    )
    hardware = (
        "Hardware spending is poised to grow at 30.56% CAGR through 2031 "
        "according to the published forecast."
    )
    assert not claim_is_supported(hardware, market, market)

    both = (
        "Hardware spending grew 30.56% while the overall market grew 29.77% "
        "through 2031 in the same forecast."
    )
    one_percent = (
        "Hardware spending grew 30.56% while the overall market grew through "
        "2031 in the same forecast."
    )
    assert not claim_is_supported(one_percent, both, both)


def test_broader_market_quote_is_kept_with_a_stand_in_limitation():
    invoicing = (
        "The enterprise software market in Germany will reach a large total by 2030 "
        "according to the published forecast note."
    )
    assert claim_is_supported(invoicing, invoicing, invoicing)
    assert stand_in_limitation(
        "Estimate invoicing software demand in Germany", invoicing, invoicing
    ) == "Source measures the broader market named in the quote, not: invoicing."

    storage = (
        "The energy market in Japan will reach a large total by 2030 "
        "according to the published forecast note."
    )
    assert stand_in_limitation(
        "Estimate battery storage demand in Japan", storage, storage
    ) == "Source measures the broader market named in the quote, not: battery, storage."


def test_markets_from_brief_keeps_europe_and_names_a_missing_estimate(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    markets = markets_from_brief(
        "Compare accelerator demand across Europe and the United States",
        "Markets: Europe, United States",
    )
    by_place = {item.place: item for item in markets}
    assert set(by_place) == {"europe", "united states"}
    assert by_place["europe"].gl == ""
    assert by_place["united states"].gl == "us"

    monkeypatch.setattr(nodes, "call_structured", lambda *_args, **_kwargs: None)
    state = empty_market_state(
        "r1",
        "Compare accelerator demand across Europe and the United States",
        "Markets: Europe, United States",
    )
    state["market_findings"] = [
        {
            "task": "Market size (United States)",
            "claim": "The United States data center market will reach a large total by 2031.",
            "confidence": 0.5,
            "evidence": [],
        }
    ]
    update = conclude(state)
    assert "No supported market estimate for: Europe" in update["market_gaps"]
    assert any(item["claim"] == "The named markets cannot be compared." for item in update["market_findings"])


def test_conclude_drops_a_sentence_that_introduces_a_percent(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    def fake(schema, *_args, **_kwargs):
        if schema is nodes.Conclusion:
            return nodes.Conclusion(
                comparison="Europe grew 12% while the United States grew 4%.",
                momentum="The growth figure in the accepted claims is a projection.",
                implication="This implies a 9% share shift for the company.",
            )
        return None

    monkeypatch.setattr(nodes, "call_structured", fake)
    state = empty_market_state(
        "r1",
        "Compare Europe and the United States",
        "Markets: Europe, United States",
    )
    state["market_findings"] = [
        {
            "task": "Market size",
            "claim": "The United States market grew 4% in 2025.",
            "evidence": [],
        }
    ]
    update = conclude(state)
    claims = [item["claim"] for item in update["market_findings"]]
    assert "Europe grew 12% while the United States grew 4%." not in claims
    assert "This implies a 9% share shift for the company." not in claims
    assert "The growth figure in the accepted claims is a projection." in claims
    momentum = next(item for item in update["market_findings"] if item["task"] == "momentum")
    assert momentum["limitations"] == ["projection"]


def test_named_retry_uses_capitalized_names_and_month_recency():
    state = empty_market_state("r1", "goal", "")
    state["task_list"] = [
        {
            "id": 1,
            "description": "Find Broadcom and Intel accelerator adoption",
            "tool_hint": "tool_serper",
            "status": "skipped",
            "search": {
                "gl": "us",
                "language": "en",
                "hl": "en",
                "place": "united states",
                "recency": "year",
            },
            "queries": [],
        }
    ]
    update = fill_gaps(state)
    task = update["task_list"][0]
    assert task["status"] == "pending"
    assert task["queries"]
    blob = json.dumps(task["queries"])
    assert "Broadcom" in blob and "Intel" in blob
    assert task["queries"][0]["recency"] == "month"
    assert update["gap_fill_done"] is True


def test_titled_hit_stores_publisher_and_date_only_when_present(sample_research_task):
    state = empty_market_state(sample_research_task.parent_request_id, "goal", "")
    state["market_findings"] = [
        {
            "task": "Market size",
            "claim": "The published forecast names the data center market.",
            "evidence": [
                {
                    "type": "url",
                    "source": "https://www.mordorintelligence.com/report",
                    "text": "The data center market forecast was published for this segment.",
                    "title": "AI Data Center Market",
                    "snippet": "Published Jan 4, 2025",
                }
            ],
        }
    ]
    state["final_report"] = {"task_summary": {"done": 1, "skipped": 0, "total": 1}}
    result = worker_result_from_state(sample_research_task, state)
    evidence = result.evidence[0]
    assert evidence.source_title == "AI Data Center Market"
    assert evidence.publisher == "Mordor Intelligence"
    assert evidence.metadata.get("published_at") == "Jan 4, 2025"
    assert "http" not in (evidence.source_title or "")

    undated = empty_market_state(sample_research_task.parent_request_id, "goal", "")
    undated["market_findings"] = [
        {
            "task": "Market size",
            "claim": "The published forecast names the data center market.",
            "evidence": [
                {
                    "type": "url",
                    "source": "https://example.com/note",
                    "text": "The data center market forecast was published for this segment.",
                    "title": "Market note",
                }
            ],
        }
    ]
    undated["final_report"] = {"task_summary": {"done": 1, "skipped": 0, "total": 1}}
    bare = worker_result_from_state(sample_research_task, undated).evidence[0]
    assert bare.publisher == "Example"
    assert "published_at" not in bare.metadata
    assert publisher_from_url("https://example.com/note") == "Example"


def test_search_query_json_keeps_region_and_recency():
    from mcp_servers.web.scrapers import _query_spec

    from edrak.agents.market_intelligence.nodes import invoke_search_tool

    class _Capture:
        def invoke(self, args):
            self.args = args
            return "[]"

    tool = _Capture()
    invoke_search_tool(
        tool,
        {"queries": [{"q": "Europe accelerator market", "gl": "", "hl": "en", "recency": "year"}]},
    )
    encoded = tool.args["queries"][0]
    spec = _query_spec(encoded)
    assert spec["q"] == "Europe accelerator market"
    assert spec["gl"] == ""
    assert spec["recency"] == "year"


def test_numeric_claim_keeps_the_year_and_forecast_word():
    quote = (
        "The software market will reach $10 billion by 2030 according to the "
        "published forecast."
    )
    missing_year = (
        "The software market will reach $10 billion according to the published "
        "forecast note."
    )
    missing_word = (
        "The software market will reach $10 billion by 2030 according to the "
        "published note today."
    )
    assert not claim_is_supported(missing_year, quote, quote)
    assert not claim_is_supported(missing_word, quote, quote)


def test_regions_without_a_country_code_are_searched_by_name():
    markets = markets_from_brief("Demand across the Middle East and Germany", "")
    by_place = {item.place: item for item in markets}
    assert by_place["middle east"].gl == ""
    assert by_place["germany"].gl == "de"
    gcc = markets_from_brief("Market size in the GCC", "")
    assert gcc[0].place == "gcc"
    assert gcc[0].gl == ""
    assert market_label(gcc[0]) == "GCC"


def test_comparison_drops_a_size_mixed_with_a_growth_rate(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    def fake(schema, *_args, **_kwargs):
        if schema is nodes.Conclusion:
            return nodes.Conclusion(
                comparison="The market is $10 billion and grew 4% in 2024.",
                momentum="Customer adoption and the market size move together.",
                implication="Northwind can serve the clinics named in the brief.",
            )
        return None

    monkeypatch.setattr(nodes, "call_structured", fake)
    state = empty_market_state(
        "r1",
        "Compare market size",
        "Company: Northwind\nTarget customers: clinics",
    )
    state["market_findings"] = [
        {
            "task": "Market size",
            "claim": "The market size is $10 billion in 2024 according to the published note.",
            "evidence": [],
        },
        {
            "task": "Growth",
            "claim": "Demand grew 4% in 2024 according to the same published note.",
            "evidence": [],
        },
    ]
    update = conclude(state)
    claims = [item["claim"] for item in update["market_findings"]]
    assert "The market is $10 billion and grew 4% in 2024." not in claims
    assert "A link between market size and adoption or technology cannot be made." in claims
    implication = next(item for item in update["market_findings"] if item["task"] == "implication")
    assert implication["limitations"] == ["analysis"]


def test_adoption_quote_without_a_stage_records_a_gap():
    quiet = "Teams are using the product across the region in large numbers today."
    assert adoption_stage_gap("Review customer adoption of the product", quiet) == (
        "Source does not say whether this is an announcement or actual use."
    )
    launched = "The product was launched for customers last year in this region."
    assert adoption_stage_gap("Review customer adoption of the product", launched) == ""


def test_planner_prompt_covers_each_named_place_and_only_asked_topics():
    text = task_planner_prompt("goal", "context", "tools", "note")
    assert "each place named in the goal" in text
    assert "only when the brief already asks" in text
