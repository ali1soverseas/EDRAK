import json

from edrak.agents.market_intelligence.graph import (
    MarketIntelligence,
    build_graph,
    run,
    task_router,
    worker_result_from_state,
)
from edrak.agents.market_intelligence.nodes import (
    clean_json,
    invoke_search_tool,
    output_node,
    task_planner,
)
from edrak.agents.market_intelligence.state import empty_market_state
from edrak.contracts import SourceType, Worker, WorkerResult, WorkerStatus, WorkerType


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


def test_market_intelligence_matches_worker_protocol():
    worker = MarketIntelligence()
    assert isinstance(worker, Worker)
    assert worker.worker_type is WorkerType.MARKET_INTELLIGENCE
    assert callable(worker.run)


def test_invoke_search_tool_sends_keyword_strings():
    seen: list[list[str]] = []

    class _StringTool:
        def invoke(self, args):
            queries = args.get("queries") or []
            seen.append(queries)
            assert all(isinstance(item, str) for item in queries)
            return json.dumps({"organic": [{"link": "https://example.com", "title": queries[0]}]})

    result = invoke_search_tool(
        _StringTool(),
        {"queries": [{"q": "automotive compliance market", "language": "en"}]},
    )
    assert seen == [["automotive compliance market"]]
    assert "automotive compliance market" in result


def test_graph_keeps_planner_executor_output_loop():
    app = build_graph()
    node_ids = set(app.get_graph().nodes)
    assert {"task_planner", "task_executor", "fill_gaps", "output_node"} <= node_ids


def test_task_router_loops_until_tasks_are_consumed():
    more_work = empty_market_state("r1", "goal", "context")
    more_work["task_list"] = [
        {"id": 1, "description": "Market size", "tool_hint": "tool_serper", "status": "pending"}
    ]
    assert task_router(more_work) == "task_executor"

    done = dict(more_work)
    done["current_task_idx"] = 1
    assert task_router(done) == "output_node"

    with_gaps = dict(done)
    with_gaps["market_gaps"] = ["No supported fact for: China market size"]
    assert task_router(with_gaps) == "fill_gaps"

    with_gaps["gap_fill_done"] = True
    assert task_router(with_gaps) == "output_node"


def test_fill_gaps_retries_skipped_tasks_in_place():
    from edrak.agents.market_intelligence.nodes import fill_gaps

    state = empty_market_state("r1", "China market entry", "GitLab")
    state["task_list"] = [
        {"id": 1, "description": "Market size", "tool_hint": "tool_serper", "status": "done", "queries": []},
        {
            "id": 2,
            "description": "Find licensing rules for foreign software in China",
            "tool_hint": "tool_web_search",
            "status": "skipped",
            "queries": [{"q": "old query"}],
        },
        {"id": 3, "description": "competitor pricing in China", "tool_hint": "tool_serper", "status": "skipped", "queries": []},
    ]
    state["market_findings"] = [{"task": "Market size", "claim": "kept"}]
    state["market_gaps"] = ["No supported fact for: Find licensing rules for foreign software in China"]
    update = fill_gaps(state)

    assert update["gap_fill_done"] is True
    assert update["market_gaps"] == []
    assert update["current_task_idx"] == 1
    assert update["task_list"][0]["status"] == "done"
    assert update["task_list"][1]["id"] == 2
    assert update["task_list"][1]["status"] == "pending"
    assert update["task_list"][1]["queries"] == []
    assert update["task_list"][2]["status"] == "pending"


def test_call_llm_prints_the_model_error(capsys, monkeypatch):
    from edrak.agents.market_intelligence import nodes

    class _Down:
        model = "gpt-oss:120b"
        base_url = "http://localhost:11434/v1"
        temperature = 0.2

        def chat_completion(self, messages, temperature=None):
            raise ConnectionError("inference API is not running")

    monkeypatch.setattr(nodes, "get_llm_client", lambda: _Down())
    result = nodes.call_llm("hello")

    assert result.startswith("[LLM ERROR:")
    assert "inference API is not running" in capsys.readouterr().out


def test_clean_json_strips_fences():
    raw = '```json\n{"useful": true}\n```'
    assert json.loads(clean_json(raw)) == {"useful": True}


def test_task_planner_keeps_every_task_in_the_reply(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    reply = json.dumps({
        "tasks": [
            {"description": "Market size in China", "tool_hint": "tool_serper"},
            {"task": "Licensing rules for foreign software", "tool": "tool_web_search"},
            "Data residency requirements",
            {"description": "Competitor presence in China", "tool_hint": "tool_hacker_news"},
        ]
    })
    monkeypatch.setattr(nodes, "call_llm", lambda _prompt: reply)
    state = empty_market_state("r1", "Assess China market conditions", "GitLab context")
    update = task_planner(state)

    assert [task["description"] for task in update["task_list"]] == [
        "Market size in China",
        "Licensing rules for foreign software",
        "Data residency requirements",
        "Competitor presence in China",
    ]
    assert update["task_list"][1]["tool_hint"] == "tool_web_search"
    assert update["task_list"][0]["search"]["gl"] == "cn"
    assert update["task_list"][0]["search"]["language"] == "zh"
    assert update["task_list"][0]["search"]["local_language"] == "Chinese"


def test_relevance_rejects_an_off_topic_page():
    from edrak.agents.market_intelligence.nodes import source_is_relevant

    ok, reason = source_is_relevant(
        "Find data residency rules for foreign DevOps software in China",
        "Assess China market conditions for foreign DevOps software",
        "United Arab Emirates packaging industry report. Video generation models on arXiv.",
    )
    assert ok is False
    assert reason


def test_relevance_accepts_competitor_pricing_without_gitlab():
    from edrak.agents.market_intelligence.nodes import source_is_relevant

    ok, _reason = source_is_relevant(
        "Map the competitive landscape and pricing models of GitHub Copilot and Amazon Q Developer",
        "GitLab, a US open-source DevSecOps platform, is considering launching an agentic AI layer",
        "GitHub Copilot Business costs $19 per user per month. Amazon Q Developer has a free tier and a Professional plan.",
    )
    assert ok is True


def test_query_prompt_stays_on_the_current_task():
    from edrak.agents.market_intelligence.prompts import tool_arg_prompt

    text = tool_arg_prompt(
        tool_name="tool_serper",
        tool_description="Google Search",
        task_description="Map the competitive landscape and pricing models of GitHub Copilot",
        context="GitLab",
        goal=(
            "GitLab is considering launching an agentic AI layer. "
            "Analyze six topics: the market size and growth of agentic AI"
        ),
        attempt=1,
        rejected_queries=["United States agentic AI market size"],
    )
    assert "market size and growth of agentic AI" not in text
    assert "Map the competitive landscape and pricing models of GitHub Copilot" in text
    assert "United States agentic AI market size" in text
    assert "do not search market size" in text.lower()


def test_search_arguments_drop_home_country_prefix():
    from edrak.agents.market_intelligence.nodes import search_arguments

    raw = json.dumps({"queries": ["United States GitHub Copilot pricing"]})
    args = search_arguments(
        raw,
        "Map GitHub Copilot pricing models",
        "GitLab, a US open-source DevSecOps platform, is considering launching an agentic AI layer",
        0,
        set(),
    )
    assert args["queries"][0]["q"].lower() == "github copilot pricing"
    assert not args["queries"][0].get("gl")


def test_search_hit_preview_ranks_task_hits():
    from edrak.agents.market_intelligence.nodes import search_hit_preview

    raw = json.dumps([
        {
            "title": "Global agentic AI market size 2034",
            "url": "https://example.com/size",
            "snippet": "The agentic AI market will reach $50 billion.",
            "score": 0.99,
        },
        {
            "title": "GitHub Copilot Business pricing",
            "url": "https://github.com/pricing",
            "snippet": "Copilot Business is $19 per user per month for agentic coding tools.",
            "score": 0.40,
        },
    ])
    preview = search_hit_preview(
        raw,
        "Map GitHub Copilot pricing models",
        "GitLab launching an agentic AI layer",
    )
    assert "GitHub Copilot Business pricing" in preview
    assert preview.index("GitHub Copilot") < preview.index("market size")


def test_search_arguments_drop_ticker_templates():
    from edrak.agents.market_intelligence.nodes import search_arguments

    raw = json.dumps({
        "queries": ["Analyze competitors pricing and packaging strategies in China for this task"],
        "tickers": ["PANW", "CRWD"],
        "series_ids": ["CPIAUCSL"],
    })
    args = search_arguments(raw, "pricing of DevOps tools in China", "China DevOps market", 0, set())
    assert "tickers" not in args
    assert "series_ids" not in args
    assert args["queries"]
    assert "panw" not in args["queries"][0]["q"].lower()
    assert args["queries"][0]["gl"] == "cn"
    assert args["queries"][0]["recency"] == "year"


def test_unsupported_claim_is_rejected():
    from edrak.agents.market_intelligence.nodes import claim_is_supported

    source = "China's Cybersecurity Law requires a security assessment for cross-border transfers."
    assert claim_is_supported(
        "China's Cybersecurity Law requires a security assessment for cross-border transfers.",
        "China's Cybersecurity Law requires a security assessment for cross-border transfers.",
        source,
    )
    assert not claim_is_supported("Data retrieved for: regulations", "", source)


def test_regulation_tasks_use_web_search():
    from edrak.agents.market_intelligence.nodes import route_tool

    assert route_tool("Find licensing rules for foreign software in China", "tool_dbnomics") == "tool_web_search"
    assert route_tool("Compare competitor prices in China", "tool_arxiv") == "tool_serper"
    assert route_tool("Enterprise customer needs including privacy and governance", "tool_serper") == "tool_serper"


def test_task_planner_falls_back_when_llm_returns_invalid_json(monkeypatch):
    from edrak.agents.market_intelligence import nodes

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


def test_log_block_writes_a_titled_section(monkeypatch):
    from edrak.agents.market_intelligence import nodes

    logged: list[str] = []
    monkeypatch.setattr(nodes, "log_action", lambda _agent, message: logged.append(message))
    nodes.log_block("market_intelligence", "GOAL", "enter mainland China")
    assert "GOAL" in logged
    assert "enter mainland China" in logged



def test_run_returns_worker_result_with_unchanged_flow(monkeypatch, sample_research_task):
    from edrak.agents.market_intelligence import graph as market_graph
    from edrak.agents.market_intelligence import nodes

    logged: list[tuple[str, str]] = []

    def capture(_agent, title, body):
        logged.append((title, body))

    monkeypatch.setattr(nodes, "log_block", capture)
    monkeypatch.setattr(market_graph, "log_block", capture)

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
                "claim": "Enterprise teams are adopting AI coding assistants across the software lifecycle.",
                "quote": "Enterprise teams are adopting AI coding assistants across the software lifecycle.",
            })
        return "{}"

    monkeypatch.setattr(nodes, "call_llm", fake_llm)
    monkeypatch.setitem(nodes.TOOL_MAP, "tool_serper", _FakeTool())
    monkeypatch.setattr(
        nodes,
        "scrape_url_content",
        lambda _url: "Enterprise teams are adopting AI coding assistants across the software lifecycle. " * 4,
    )

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
    assert result.confidence == 0.55
    assert result.findings[0].confidence == 0.55
    assert "adopting AI coding assistants" in (result.evidence[0].extracted_fact or "")
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.completed_at >= result.started_at
    assert result.error == "none"
    titles = [title for title, _body in logged]
    assert "GOAL" in titles
    assert "BUSINESS CONTEXT" in titles
    assert any(title.startswith("SCRAPED URL") and "https://example.com/ai-devops" in title for title, _body in logged)
    assert any("kept" in body for title, body in logged if title.startswith("SCRAPED URL"))
    assert any(title == "OUTPUT" and sample_research_task.task_id in body for title, body in logged)


def test_search_arguments_keep_local_language_queries():
    from edrak.agents.market_intelligence.nodes import search_arguments

    raw = json.dumps({
        "queries": [
            {"q": "China DevOps market size", "language": "en", "include_domains": ["idc.com"]},
            "中国 DevOps 市场规模",
        ]
    })
    args = search_arguments(raw, "China DevOps market size", "mainland China market entry", 0, set())
    languages = [item["language"] for item in args["queries"]]

    assert languages == ["en", "zh"]
    assert all(item["gl"] == "cn" for item in args["queries"])
    assert args["queries"][0]["include_domains"] == ["idc.com"]
    assert "include_domains" not in args["queries"][1]
    assert args["queries"][1]["hl"] == "zh-cn"


def test_scope_follows_the_brief_and_not_one_country():
    from edrak.agents.market_intelligence.search_scope import scope_from_brief

    japan = scope_from_brief("Enter the Japan market", "GitLab is a United States company")
    assert japan.gl == "jp"
    assert japan.language == "ja"
    assert japan.needs_local_queries is True

    united_states = scope_from_brief("Demand among US enterprises", "GitLab")
    assert united_states.gl == "us"
    assert united_states.language == "en"
    assert united_states.needs_local_queries is False

    this_week = scope_from_brief("China DevOps news this week", "")
    assert this_week.gl == "cn"
    assert this_week.recency == "week"

    launch = scope_from_brief(
        "GitLab, a US open-source DevSecOps platform, is considering launching an agentic AI layer",
        "GitLab is a United States company",
    )
    assert launch.gl == ""
    assert launch.place == ""
    assert launch.needs_local_queries is False


def test_source_tiers_are_configurable():
    from edrak.agents.market_intelligence.source_tiers import SourceTier, source_weight

    assert source_weight("https://www.miit.gov.cn/notice") > 0
    assert source_weight("https://www.gartner.com/reviews") > 0
    assert source_weight("https://www.g2.com/products") < 0
    assert source_weight("https://medium.com/post") < 0

    custom = (
        SourceTier("regulator", -1.0, (".gov",)),
        SourceTier("reseller", 1.0, ("g2.com",)),
    )
    assert source_weight("https://www.miit.gov.cn/notice", custom) == -1.0
    assert source_weight("https://www.g2.com/products", custom) == 1.0
    assert source_weight("https://www.gartner.com/reviews", custom) == 0.0


def test_ranked_urls_prefer_scope_and_source_tier_over_raw_order():
    from edrak.agents.market_intelligence.nodes import ranked_urls
    from edrak.agents.market_intelligence.search_scope import scope_from_brief
    from edrak.agents.market_intelligence.source_tiers import SourceTier

    raw = json.dumps([
        {
            "title": "Best DevOps tools",
            "url": "https://www.g2.com/devops",
            "snippet": "China devops regulation market",
            "score": 0.95,
        },
        {
            "title": "MIIT notice",
            "url": "https://www.miit.gov.cn/notice",
            "snippet": "China devops regulation market",
            "score": 0.40,
        },
    ])
    scope = scope_from_brief("China devops regulation market")
    ranked = ranked_urls(raw, "China devops regulation", "China devops market", scope=scope)
    assert ranked[0] == "https://www.miit.gov.cn/notice"

    flipped = (
        SourceTier("regulator", -1.0, (".gov",)),
        SourceTier("reseller", 1.0, ("g2.com",)),
    )
    reranked = ranked_urls(
        raw,
        "China devops regulation",
        "China devops market",
        scope=scope,
        tiers=flipped,
    )
    assert reranked[0] == "https://www.g2.com/devops"


def test_titles_on_topic_skips_off_task_overviews():
    from edrak.agents.market_intelligence.nodes import titles_on_topic

    raw = json.dumps([
        {
            "title": "GitHub Copilot Business pricing",
            "url": "https://github.com/pricing",
            "snippet": "Copilot Business is $19 per user per month.",
        },
        {
            "title": "Amazon Q Developer pricing",
            "url": "https://aws.amazon.com/q/pricing",
            "snippet": "Amazon Q Developer offers a free tier and a Professional plan.",
        },
    ])
    assert titles_on_topic(
        raw,
        "Map GitHub Copilot and Amazon Q Developer pricing",
        "GitLab launching an agentic AI layer",
    )


def test_page_excerpt_is_the_extracted_fact():
    from edrak.agents.market_intelligence.graph import _evidence_from_private_item

    evidence = _evidence_from_private_item(
        {
            "type": "url",
            "source": "https://github.com/pricing",
            "text": "Copilot Business is $19 per user per month for organizations.",
        },
        quote="A different quote from another page about Huawei CodeArts.",
    )
    assert "Copilot Business is $19" in (evidence.extracted_fact or "")
    assert "Huawei" not in (evidence.extracted_fact or "")


def test_search_calls_carry_region_language_and_recency():
    from mcp_servers.web.scrapers import _serper_body, _tavily_body

    spec = {
        "q": "devops market",
        "gl": "jp",
        "hl": "ja",
        "recency": "year",
        "tavily_country": "japan",
        "include_domains": ["meti.go.jp"],
    }
    serper = _serper_body(spec, with_domains=True)
    assert serper["gl"] == "jp"
    assert serper["hl"] == "ja"
    assert serper["tbs"] == "qdr:y"
    assert "site:meti.go.jp" in serper["q"]

    tavily = _tavily_body(spec, "test-key", with_domains=True)
    assert tavily["time_range"] == "year"
    assert tavily["country"] == "japan"
    assert tavily["include_domains"] == ["meti.go.jp"]


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
    assert result.confidence == 0.35
    assert result.findings[0].confidence == 0.35
    assert result.started_at is not None
    assert result.completed_at is not None
    assert result.error == "Some research tasks were skipped."
