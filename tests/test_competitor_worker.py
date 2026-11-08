"""The competitor worker: relevant sources only, a bounded loop, grounded findings, a comparison."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from edrak.agents.competitor_intelligence import nodes, sources
from edrak.agents.competitor_intelligence.socials import (
    normalise_profile,
    profiles_in_html,
)
from edrak.agents.competitor_intelligence.state import (
    MAX_QUERIES_TOTAL,
    MAX_RESEARCH_STAGES,
    ComparisonReport,
    CompetitorAssessment,
    CompetitorProfile,
    ConsolidatedRequirement,
    DimensionComparison,
    FindingList,
    LLMFinding,
    ResearchRequirement,
    ResearchRequirements,
    SearchQuery,
    SearchQueryList,
)
from edrak.contracts import (
    BusinessContext,
    CompanyProfile,
    CompetitorRef,
    FindingCategory,
    ResearchTask,
    SourceType,
    UseCase,
    WorkerStatus,
    WorkerType,
)

PRICING_TEXT = (
    "Kanz Pay pricing. The Starter plan costs 99 EGP per month and includes 50 invoices. "
    "The Growth plan costs 299 EGP per month with unlimited invoices and Arabic e-invoicing support. "
) * 3


# ------------------------------------------------------------------ sources


def test_sources_keep_the_official_site_and_drop_farms_social_and_off_topic_pages():
    sources.register_official_domains("Kanz Pay", ["https://www.kanzpay.example"])
    official = {"url": "https://kanzpay.example/pricing", "title": "Pricing", "text": PRICING_TEXT}
    assert sources.judge(official, "Kanz Pay") == (True, "official")
    farm = {"url": "https://www.eesel.ai/blog/kanz-pay", "title": "Kanz Pay review", "text": PRICING_TEXT}
    assert sources.judge(farm, "Kanz Pay")[0] is False
    social = {"url": "https://reddit.com/r/egypt/x", "title": "Kanz Pay?", "text": PRICING_TEXT}
    assert sources.judge(social, "Kanz Pay")[0] is False
    off_topic = {"url": "https://news.example/fintech", "title": "Fintech in Egypt", "text": "Banks grew. " * 40}
    assert sources.judge(off_topic, "Kanz Pay") == (False, "does not name the competitor")
    press = {"url": "https://news.example/kanzpay-raises", "title": "KanzPay raises a round", "text": "KanzPay " + "x " * 200}
    assert sources.judge(press, "Kanz Pay") == (True, "relevant")


def test_without_a_registered_domain_the_name_in_the_host_counts_as_official():
    assert sources.is_official("https://www.sadapay.example/plans", "SadaPay")
    assert not sources.is_official("https://blog.example/sadapay", "SadaPay")


def test_best_passage_picks_the_supporting_sentence():
    passage = sources.best_passage(PRICING_TEXT, "The Growth plan costs 299 EGP per month")
    assert "299 EGP" in passage and len(passage) <= 500


# ------------------------------------------------------------------ socials


def test_profiles_are_read_from_links_and_share_buttons_are_ignored():
    html = """
      <a href="https://twitter.com/intent/tweet?text=hi">share</a>
      <a href="https://x.com/kanzpay">X</a>
      <a href="https://www.facebook.com/sharer/sharer.php?u=x">share</a>
      <a href="https://facebook.com/KanzPayEG/">Facebook</a>
      <a href="https://www.instagram.com/p/abc123/">a post</a>
      <a href="https://instagram.com/kanzpay.eg">Instagram</a>
      <a href="https://www.linkedin.com/company/kanz-pay/about/">LinkedIn</a>
    """
    found = profiles_in_html(html, "https://kanzpay.example")
    assert found == {
        "x": "https://x.com/kanzpay",
        "facebook": "https://www.facebook.com/KanzPayEG",
        "instagram": "https://www.instagram.com/kanzpay.eg",
        "linkedin": "https://www.linkedin.com/company/kanz-pay",
    }
    assert normalise_profile("https://x.com/kanzpay/status/123") == "https://x.com/kanzpay"
    assert normalise_profile("https://www.youtube.com/watch?v=1") is None


# ------------------------------------------------------------------ grounding and the loop


def test_grounding_refuses_invented_numbers_and_unsupported_claims():
    assert nodes.grounding_problem("The Growth plan costs 299 EGP per month.", PRICING_TEXT) is None
    assert "349" in nodes.grounding_problem("The Growth plan costs 349 EGP per month.", PRICING_TEXT)
    assert nodes.grounding_problem("Kanz Pay offers payroll for enterprises in Saudi Arabia.", PRICING_TEXT)


def test_the_router_stops_on_stages_budget_and_lack_of_progress():
    open_req = [{"requirement_id": "R1", "competitor": "A", "requirement": "x"}]
    assert nodes.research_router({"missing_information": []}) == "finish"
    assert nodes.research_router({"missing_information": open_req, "research_stage": MAX_RESEARCH_STAGES}) == "finish"
    budget = [{"query": str(i)} for i in range(MAX_QUERIES_TOTAL)]
    assert nodes.research_router({"missing_information": open_req, "research_stage": 1, "executed_queries": budget}) == "finish"
    assert nodes.research_router({"missing_information": open_req, "research_stage": 2, "new_verified_this_stage": 0}) == "finish"
    assert nodes.research_router({"missing_information": open_req, "research_stage": 1, "new_verified_this_stage": 0}) == "research_again"


def test_prior_graph_context_is_scoped_to_the_assigned_task(monkeypatch):
    from edrak.graph import agent_context
    from edrak.graph.loader import load_runs
    from edrak.graph.retrieval.graph_retriever import GraphGap

    task = next(t for t in load_runs()[0].plan.tasks if t.worker == WorkerType.COMPETITOR_INTELLIGENCE)
    calls = []

    class Retriever:
        def available(self):
            return True

        def find_findings(self, workspace_id, **kwargs):
            calls.append((workspace_id, kwargs))
            return []

        def signals(self, *args, **kwargs):
            return []

        def open_gaps(self, *args, **kwargs):
            return [
                GraphGap("a", "AMD accelerator pricing", None, None, "open", None),
                GraphGap("b", "Unrelated company pricing", None, None, "open", None),
            ]

        def metric_history(self, *args, **kwargs):
            return []

    monkeypatch.setattr(agent_context, "_add_history", lambda *args: None)
    prior = agent_context.prior_knowledge_for_task(task, retriever=Retriever())
    assert calls[0][0] == "sandbox"
    assert calls[0][1]["entity_ids"] == ["amd", "broadcom", "intel"]
    assert calls[0][1]["exclude_request_id"] == task.parent_request_id
    assert calls[0][1]["include_synthetic"] is False
    assert task.goal in calls[0][1]["query"] and task.focus in calls[0][1]["query"]
    assert [gap.id for gap in prior.gaps] == ["a"]


def test_discovered_competitors_scope_prior_context_and_only_guide_planning(monkeypatch):
    import edrak.agents.competitor_intelligence as competitor
    from edrak.agents.competitor_intelligence.prompts import (
        queries_prompt,
        requirements_prompt,
    )
    from edrak.graph import agent_context
    task = ResearchTask(
        task_id="competitor-task",
        parent_request_id="run-1",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Compare pricing",
        focus="Enterprise pricing",
        company_profile=CompanyProfile(name="Acme", workspace_id="workspace-1"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    scoped = []
    monkeypatch.setattr(competitor, "_competitors_for", lambda task: (["Kanz Pay"], {}))
    monkeypatch.setattr("edrak.graph.connection.get_graph_settings", lambda: SimpleNamespace(GRAPH_ENABLED=True))
    monkeypatch.setattr(agent_context, "current_prior_knowledge", lambda task: None)

    def prior_lookup(task):
        scoped.append(task)
        return SimpleNamespace(as_prompt=lambda: "Earlier Kanz Pay pricing may have changed.")

    monkeypatch.setattr(agent_context, "prior_knowledge_for_task", prior_lookup)
    state = competitor._task_to_initial_state(task)
    assert scoped[0].business_context.targets == ["Kanz Pay"]
    assert scoped[0].company_profile.workspace_id == "workspace-1"
    assert scoped[0].parent_request_id == "run-1"
    assert state["prior_knowledge"] == "Earlier Kanz Pay pricing may have changed."
    assert state["verified_findings"] == []
    assert "Earlier Kanz Pay pricing" in requirements_prompt(state)
    prompt = queries_prompt(state, 1, "discovery", [], [], [], "")
    assert "Earlier Kanz Pay pricing" in prompt
    assert "Collect fresh sources" in prompt


def test_competitor_without_named_entities_does_not_read_unscoped_history(monkeypatch):
    from edrak.graph.agent_context import prior_knowledge_for_task

    class Retriever:
        def available(self):
            return True

        def find_findings(self, *args, **kwargs):
            pytest.fail("unscoped graph search")

    task = ResearchTask(
        task_id="task",
        parent_request_id="run",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Compare pricing",
        focus="Pricing",
        company_profile=CompanyProfile(name="Acme", workspace_id="ws"),
        business_context=BusinessContext(use_case=UseCase.COMPETITIVE_INTELLIGENCE),
    )
    assert prior_knowledge_for_task(task, retriever=Retriever()).is_empty


def test_wrapper_reports_context_discovered_during_worker_run(monkeypatch):
    from edrak.graph import agent_context
    from edrak.graph.retrieval.graph_retriever import GraphGap

    empty = agent_context.PriorKnowledge(worker="competitor_intelligence")
    discovered = agent_context.PriorKnowledge(
        worker="competitor_intelligence",
        gaps=[GraphGap("gap", "Kanz Pay pricing", None, None, "open", None)],
    )
    task = SimpleNamespace(task_id="task", attempt=1, worker="competitor_intelligence")
    monkeypatch.setattr(agent_context, "prior_knowledge_for_task", lambda *args, **kwargs: empty)

    class Worker:
        worker_type = WorkerType.COMPETITOR_INTELLIGENCE

        def run(self, task):
            agent_context.update_current_prior_knowledge(task, discovered)
            return SimpleNamespace(metadata={})

    result = agent_context.GraphAwareWorker(Worker(), focus_hints=False).run(task)
    assert result.metadata["graph_prior"]["gaps"] == 1
    assert agent_context.current_prior_knowledge(task) is None


# ------------------------------------------------------------------ the whole graph, offline


class FakeTavily:
    def __init__(self):
        self.calls: list[dict] = []

    def search(self, **params):
        self.calls.append(params)
        return {
            "results": [
                {"url": "https://kanzpay.example/pricing", "title": "Kanz Pay pricing", "content": PRICING_TEXT},
                {"url": "https://www.eesel.ai/blog/kanz-pay-alternatives", "title": "Kanz Pay alternatives", "content": PRICING_TEXT},
            ]
        }


def fake_structured(prompt, model_class, strong=False, retries=1):
    if model_class is ResearchRequirements:
        return ResearchRequirements(requirements=[
            ResearchRequirement(competitor="Kanz Pay", requirement=f"Pricing plan detail {i}") for i in range(9)
        ])
    if model_class is SearchQueryList:
        return SearchQueryList(queries=[
            SearchQuery(requirement_id=f"R{i}", query=f"Kanz Pay pricing plans {i}", purpose="pricing") for i in range(1, 6)
        ])
    if model_class is FindingList:
        return FindingList(findings=[
            LLMFinding(competitor="Kanz Pay", requirement_id="R1", finding="The Growth plan costs 299 EGP per month.",
                       category="pricing_packaging", evidence_source_ids=["S1"]),
            LLMFinding(competitor="Kanz Pay", requirement_id="R2", finding="Kanz Pay has 10,000 enterprise customers.",
                       category="market_signal", evidence_source_ids=["S1"]),
        ])
    if model_class is CompetitorProfile:
        return CompetitorProfile(overview="Kanz Pay sells invoicing plans.", requirements=[
            ConsolidatedRequirement(requirement_id="R1", summary="Growth is 299 EGP per month.", evidence_source_ids=["S1"]),
        ])
    if model_class is ComparisonReport:
        return ComparisonReport(
            executive_summary="Comparable pricing structures.",
            dimensions=[DimensionComparison(
                dimension="Pricing and packaging",
                target_position="Our entry plan is 89 EGP.",
                assessments=[CompetitorAssessment(competitor="Kanz Pay", summary="Kanz Pay charges 99 EGP for Starter.",
                                                  relative_to_target="comparable", confidence="medium",
                                                  requirement_ids=["R1"])],
                takeaway="Both sell monthly plans.",
            )],
        )
    raise AssertionError(model_class)


@pytest.fixture
def offline(monkeypatch):
    fake = FakeTavily()
    monkeypatch.setattr(nodes, "tavily", lambda: fake)
    monkeypatch.setattr(nodes, "call_structured", fake_structured)
    monkeypatch.setattr(nodes._settings, "EDRAK_DEBUG", False)
    return fake


def test_a_run_is_bounded_grounded_and_returns_the_comparison(offline):
    from edrak.agents.competitor_intelligence import run_research

    task = ResearchTask(
        parent_request_id="r1",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="How does our pricing compare with Kanz Pay?",
        focus="Pricing",
        company_profile=CompanyProfile(name="Nile Ledger", products=["Invoicing"]),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            targets=["Kanz Pay"],
            competitors=[CompetitorRef(name="Kanz Pay", website="https://kanzpay.example")],
            markets=["Egypt"],
        ),
    )
    result = run_research(task, print_result=False)

    assert result.status in (WorkerStatus.COMPLETED, WorkerStatus.PARTIAL)
    assert result.metadata["research_stages_completed"] <= MAX_RESEARCH_STAGES
    assert len(offline.calls) <= MAX_QUERIES_TOTAL + 5   # official-site retries are the only extra calls
    # At most five requirements per competitor were kept from the nine proposed.
    assert len(result.metadata["requirement_checks"]) <= 5

    statements = [f.statement for f in result.findings]
    assert any("299 EGP" in s for s in statements)
    # The invented customer count is not in the sources, so it was refused.
    assert not any("10,000" in s for s in statements)
    assert any(f.category is FindingCategory.PRICING_PACKAGING for f in result.findings)
    assert any(f.category is FindingCategory.POSITIONING for f in result.findings)

    # Only cited sources are evidence, the content farm never is, and the fact is a passage.
    urls = {e.source_url for e in result.evidence}
    assert urls == {"https://kanzpay.example/pricing"}
    assert result.evidence[0].source_type is SourceType.PRICING_PAGE
    assert len(result.evidence[0].extracted_fact) <= 500

    matrix = result.metadata["comparison_matrix"]
    assert matrix["dimensions"][0]["cells"][0]["relative_to_target"] == "comparable"
    assert matrix["dimensions"][0]["cells"][0]["evidence_ids"]
    assert result.metadata["competitors"][0]["official_domains"] == ["kanzpay.example"]


def test_prior_context_guides_search_without_becoming_evidence(offline, monkeypatch):
    from edrak.agents.competitor_intelligence import run_research
    from edrak.graph import agent_context

    prompts = {}

    def capture_prompt(prompt, model_class, **kwargs):
        prompts.setdefault(model_class, []).append(prompt)
        return fake_structured(prompt, model_class, **kwargs)

    monkeypatch.setattr(nodes, "call_structured", capture_prompt)
    monkeypatch.setattr("edrak.graph.connection.get_graph_settings", lambda: SimpleNamespace(GRAPH_ENABLED=True))
    monkeypatch.setattr(
        agent_context, "current_prior_knowledge",
        lambda task: SimpleNamespace(as_prompt=lambda: "Historical pricing: Kanz Pay charged 42 EGP."),
    )
    task = ResearchTask(
        parent_request_id="run",
        worker=WorkerType.COMPETITOR_INTELLIGENCE,
        goal="Compare pricing",
        focus="Pricing",
        company_profile=CompanyProfile(name="Nile Ledger", workspace_id="workspace-1"),
        business_context=BusinessContext(
            use_case=UseCase.COMPETITIVE_INTELLIGENCE,
            competitors=[CompetitorRef(name="Kanz Pay", website="https://kanzpay.example")],
        ),
    )
    result = run_research(task, print_result=False)
    assert "Historical pricing" in prompts[ResearchRequirements][0]
    assert "Historical pricing" in prompts[SearchQueryList][0]
    assert offline.calls
    assert all("42 EGP" not in finding.statement for finding in result.findings)
    assert all("42 EGP" not in evidence.extracted_fact for evidence in result.evidence)
