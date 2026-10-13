"""Figures must measure the right thing: the right market, the right topic, the right kind of number."""

from __future__ import annotations

from edrak.agents.metrics import nodes as agent
from edrak.api.analysis_service import split_markets
from edrak.briefing.extract import pick_number
from edrak.contracts import Evidence
from edrak.contracts.evidence import SourceType
from edrak.metrics.contracts import MetricRequirement
from edrak.metrics.planning import requirements_for
from edrak.metrics.profile import HARDWARE, business_model
from edrak.metrics.registry import get_registry


def test_markets_in_brackets_are_split_and_the_rest_is_the_topic():
    markets, topic = split_markets("Emerging Sovereign AI Markets (Southeast Asia, Latin America, Africa)")
    assert markets == ["Southeast Asia", "Latin America", "Africa"]
    assert topic == "Sovereign AI"
    assert split_markets("Egypt (Cairo), Saudi Arabia; UAE")[0] == ["Egypt (Cairo)", "Saudi Arabia", "UAE"]
    assert split_markets("Africa)")[0] == ["Africa"]


def test_a_year_is_never_the_value():
    assert pick_number("In 2024, the market reached $2.3 billion", "currency").value == 2.3e9
    assert pick_number("Founded in 2024 with big plans", "currency") is None
    assert pick_number("In 2025, 21.3% of firms adopted it", "percent").value == 21.3
    assert pick_number("Adoption grew in 2025", "percent") is None
    assert pick_number("The deal closed in 2024 for $150M", "currency", value_text="$150M").value == 150e6


def test_stock_pages_and_explainers_are_never_read():
    assert agent.is_noise("https://finance.yahoo.com/quote/AMD/")
    assert agent.is_noise("https://stocktwits.com/symbol/INTC/sentiment")
    assert not agent.is_noise("https://finance.yahoo.com/technology/ai/articles/sovereign-ai-market-reach-usd-151500186.html")
    assert agent.is_explainer("Need to know: What is share of voice?")
    assert not agent.is_explainer("What is the Sovereign AI Infrastructure Market Size in 2026?")


def test_a_market_figure_must_be_about_the_topic():
    defn = get_registry().get("market_cagr")
    retail = Evidence(evidence_id="e1", source_type=SourceType.WEB_PAGE, source_title="Latin America Retail Platform Market",
                      source_url="https://example.com/retail", extracted_fact="x",
                      page_text="The Latin America retail platform market will grow at a CAGR of 21.3% from 2025 to 2030.")
    sovereign = Evidence(evidence_id="e2", source_type=SourceType.WEB_PAGE, source_title="Sovereign AI in Latin America",
                         source_url="https://example.com/sov", extracted_fact="x",
                         page_text="Sovereign AI spending in Latin America will grow at a CAGR of 18% from 2025 to 2030.")
    got = agent.windows_for(defn, ["Latin America"], [retail, sovereign], topic="Sovereign AI")
    assert [ev.evidence_id for ev, _ in got] == ["e2"]


def test_hardware_companies_get_the_infrastructure_metric_set():
    profile = {"notes": "NVIDIA designs GPUs and accelerated computing platforms for data centers.", "products": ["Blackwell GPU"]}
    assert business_model(profile) == HARDWARE
    assert business_model({"notes": "A SaaS project management tool sold per seat."}) is None
    reqs = requirements_for("market_intelligence", "market_entry_expansion", "NVIDIA", [], ["Africa"], "sovereign AI", HARDWARE)
    ids = {r.metric_id for r in reqs}
    assert "infra_investment" in ids and "search_interest" not in ids
    assert all("{" not in h for r in reqs for h in r.query_hints)
    assert get_registry().template("market_entry_expansion@hardware_infrastructure").key.endswith(HARDWARE)


def test_counted_and_internal_figures_are_never_web_searched():
    reg = get_registry()
    assert not agent.huntable(reg.get("sentiment_split"))
    assert not agent.huntable(reg.get("search_interest"))
    assert not agent.huntable(reg.get("capacity"))
    assert agent.huntable(reg.get("market_size"))


def test_searches_name_the_topic_and_skip_short_acronyms():
    defn = get_registry().get("market_size")
    r = MetricRequirement(metric_id="market_size", entity="Latin America", query_hints=["Latin America market size"])
    first = agent._queries(r, defn, "sovereign AI", 1)
    assert all("sovereign AI" in q for q in first)
    second = agent._queries(r, defn, "sovereign AI", 2)
    assert not any(" tam" in q.lower() for q in second)


def test_each_onboarding_industry_gets_its_own_metric_set():
    from edrak.metrics.profile import DYNAMIC, EDUCATION, FINANCE, HEALTH, LOGISTICS, RETAIL
    assert business_model({"industries": ["fintech"]}) == FINANCE
    assert business_model({"industries": ["retail"]}) == RETAIL
    assert business_model({"industries": ["logistics"]}) == LOGISTICS
    assert business_model({"industries": ["healthcare"]}) == HEALTH
    assert business_model({"industries": ["education"]}) == EDUCATION
    assert business_model({"industries": ["manufacturing"]}) == HARDWARE
    assert business_model({"industries": ["saas"]}) is None
    assert business_model({"industries": ["technology"], "notes": "We design GPUs and data center systems."}) == HARDWARE
    assert business_model({"industries": ["other"], "industry_other": "Boutique hotels"}) == DYNAMIC
    reg = get_registry()
    fin = {r.metric_id for r in requirements_for("competitor_intelligence", "competitive_intelligence", "Acme", ["Fawry"], [], "", FINANCE)}
    assert "fee_rate" in fin and "price_per_tier" not in fin
    for uc in ("competitive_intelligence", "market_entry_expansion", "product_launch"):
        for model in (FINANCE, RETAIL, LOGISTICS, HEALTH, EDUCATION, HARDWARE, DYNAMIC):
            tpl = reg.template(uc, model)
            assert tpl.key == f"{uc}@{model}" and tpl.required and all(reg.get(m) for m in tpl.kpi_strip)


def test_planner_proposals_become_metrics_on_top_of_the_core():
    from edrak.metrics.custom import register, to_defs
    defs = to_defs([
        {"name": "Hotel occupancy rate", "definition": "Share of rooms sold.", "unit_kind": "percent",
         "owner": "market_intelligence", "search_phrase": "hotel occupancy rate", "reads_as": "How full hotels are."},
        {"name": "Vibe score", "definition": "x", "unit_kind": "index", "owner": "market_intelligence", "search_phrase": "vibe"},
        {"name": "Market size", "definition": "dup", "unit_kind": "currency", "owner": "market_intelligence", "search_phrase": "size"},
        {"oops": True},
    ])
    assert [d.id for d in defs] == ["x_hotel_occupancy_rate"]  # no index, no duplicate of a known metric, no malformed item
    ids = register(defs)
    tpl = get_registry().template("market_entry_expansion", "dynamic", ids)
    assert tpl.kpi_strip[0] == "x_hotel_occupancy_rate" and "x_hotel_occupancy_rate" in tpl.required
    assert "market_size" in tpl.required
    reqs = requirements_for("market_intelligence", "market_entry_expansion", "Acme", [], ["Egypt"], "boutique hotels", "dynamic", ids)
    occ = next(r for r in reqs if r.metric_id == "x_hotel_occupancy_rate")
    assert occ.query_hints == ["Egypt hotel occupancy rate"]
