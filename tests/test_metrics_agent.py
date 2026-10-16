"""The metrics agent: harvest from saved page text, a bounded hunt, gap reasons, the proxy tag, stance counts."""

import pytest

from edrak.agents.metrics import nodes as agent
from edrak.agents.metrics import run
from edrak.briefing.extract import Candidate, Candidates
from edrak.contracts import (
    BusinessContext,
    BusinessRequest,
    CompanyProfile,
    Evidence,
    ResearchTask,
    WorkerResult,
    WorkerStatus,
    WorkerType,
)
from edrak.contracts.evidence import SourceType
from edrak.metrics.contracts import MetricRequirement


@pytest.fixture(autouse=True)
def _restore():
    yield
    agent.use()


def _request():
    return BusinessRequest(goal="How do we compare?", company_profile=CompanyProfile(name="Acme"),
                           business_context=BusinessContext(use_case="competitive_intelligence"))


def _task(worker, reqs, tid):
    return ResearchTask(task_id=tid, parent_request_id="r", worker=worker, goal="g", focus="f",
                        company_profile=CompanyProfile(name="Acme"),
                        business_context=BusinessContext(use_case="competitive_intelligence"), requirements=reqs)


def _price(entity):
    return MetricRequirement(metric_id="price_per_tier", entity=entity, preferred_source_types=["official"],
                             query_hints=[f"{entity} pricing"])


def _finder_for(span, entity, evidence_id=None):
    """A fake model that points at `span` in whichever passage contains it."""
    def find(prompt):
        if span not in prompt:
            return Candidates(metrics=[])
        before = prompt[: prompt.index(span)]
        eid = evidence_id or before[before.rindex("[") + 1: before.rindex("]")]
        return Candidates(metrics=[Candidate(metric_id="price_per_tier", entity=entity, evidence_id=eid, source_span=span)])
    return find


def test_harvest_finds_a_price_far_down_the_saved_page():
    page = "Intro. " + "Words about features. " * 500 + "The Team plan costs $19 per user per month."
    ev = Evidence(evidence_id="e1", source_type=SourceType.PRICING_PAGE, source_url="https://www.globex.com/pricing",
                  extracted_fact="Intro.", excerpt=page[:1500], page_text=page)
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1, evidence=[ev])
    agent.use(find=_finder_for("Team plan costs $19 per user", "Globex"), search=lambda q: [], fetch=lambda u: "")
    fixed, _ = run(_request(), [task], [res])
    m = fixed[0].metadata["metrics"][0]
    assert (m["value"], m["status"], m["scope"]) == (19.0, "verified", "official")
    assert fixed[0].metadata["requirement_outcomes"][0]["status"] == "found"


def test_hunt_runs_up_to_max_hunts_and_ends_in_a_gap_with_a_reason():
    calls = []
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1)

    def search(q):
        calls.append(q)
        return [{"url": f"https://news{len(calls)}.example.com/a", "title": "x", "snippet": "no figure"}]

    agent.use(find=lambda p: Candidates(metrics=[]), search=search, fetch=lambda u: "An article with no price at all.")
    fixed, _ = run(_request(), [task], [res])
    out = fixed[0].metadata["requirement_outcomes"][0]
    assert out["status"] == "gap" and out["reason"] == "not_found" and out["attempts"] == agent.MAX_HUNTS + 1
    assert 1 <= len(calls) <= agent.MAX_HUNTS * 2  # up to MAX_HUNTS rounds, at most two queries each


def test_reading_the_entitys_own_pages_without_a_figure_is_not_publicly_disclosed():
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1)
    n = iter(range(100))
    agent.use(find=lambda p: Candidates(metrics=[]),
              search=lambda q: [{"url": f"https://www.globex.com/p{next(n)}", "title": "Plans", "snippet": "Contact sales"}],
              fetch=lambda u: "Plans. Contact sales for a quote.")
    fixed, _ = run(_request(), [task], [res])
    assert fixed[0].metadata["requirement_outcomes"][0]["reason"] == "not_publicly_disclosed"
    assert any(e.metadata.get("found_by") == "metrics" for e in fixed[0].evidence)


def test_hunt_finds_the_figure_on_the_official_page():
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1)
    agent.use(find=_finder_for("Pro is $30 per user", "Globex"),
              search=lambda q: [{"url": "https://blog.example.com/x", "title": "b", "snippet": ""},
                                {"url": "https://globex.com/pricing", "title": "Pricing", "snippet": ""}],
              fetch=lambda u: "Globex pricing. Pro is $30 per user per month." if "globex" in u else "Blog text.")
    fixed, _ = run(_request(), [task], [res])
    m = fixed[0].metadata["metrics"][0]
    assert m["value"] == 30.0 and m["status"] == "verified"
    assert fixed[0].metadata["requirement_outcomes"][0]["status"] == "found"


def test_our_own_public_figure_is_tagged_public_proxy_for_internal():
    own = MetricRequirement(metric_id="own_price", entity="Acme", query_hints=["Acme pricing"])
    task = _task(WorkerType.INTERNAL_INTELLIGENCE, [own], "t1")
    ev = Evidence(evidence_id="e1", source_type=SourceType.PRICING_PAGE, source_url="https://acme.com/pricing",
                  extracted_fact="Acme Business is $25 per user per month.")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1, evidence=[ev])

    def find(prompt):
        return Candidates(metrics=[Candidate(metric_id="own_price", entity="Acme", evidence_id="e1", source_span="Business is $25 per user")])

    agent.use(find=find, search=lambda q: [], fetch=lambda u: "")
    fixed, _ = run(_request(), [task], [res])
    assert fixed[0].metadata["metrics"][0]["scope"] == "public_proxy_for_internal"


def test_sentiment_is_counted_in_code_from_stance_labels():
    reqs = [MetricRequirement(metric_id="sentiment_split", entity=e) for e in ("Acme", "Globex")]
    reqs.append(MetricRequirement(metric_id="share_of_voice", entity="Acme"))
    task = _task(WorkerType.CUSTOMER_TRENDS, reqs, "t1")
    counts = {"Acme": {"positive": 20, "neutral": 6, "negative": 12, "mixed": 2, "total": 40},
              "Globex": {"positive": 3, "neutral": 1, "negative": 6, "mixed": 0, "total": 10}}
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1,
                       metadata={"stance_counts": counts, "control_summary": {"coverage": {"by_platform": {"x": 30, "reddit": 20}}}})
    agent.use(find=lambda p: pytest.fail("no model call for counted figures"), search=lambda q: [], fetch=lambda u: "")
    fixed, _ = run(_request(), [task], [res])
    ms = fixed[0].metadata["metrics"]
    acme = {m["label"]: m for m in ms if m["metric_id"] == "sentiment_split" and m["entity"] == "Acme"}
    assert acme["positive"]["value"] == 50.0 and acme["positive"]["sample_size"] == 40
    assert acme["positive"]["status"] == "verified" and acme["positive"]["derived"]
    globex = [m for m in ms if m["metric_id"] == "sentiment_split" and m["entity"] == "Globex"]
    assert all(m["status"] == "thin" for m in globex)  # 10 items is below the minimum sample
    sov = {m["entity"]: m["value"] for m in ms if m["metric_id"] == "share_of_voice"}
    assert sov == {"Acme": 80.0, "Globex": 20.0}
    outcomes = {(o["metric_id"], o["entity"]): o for o in fixed[0].metadata["requirement_outcomes"]}
    assert outcomes[("sentiment_split", "Acme")]["status"] == "found"
    assert outcomes[("sentiment_split", "Globex")]["reason"] == "below_min_sample"


def test_customer_stances_keep_only_the_listed_entities():
    from types import SimpleNamespace

    from edrak.agents.customer_trends.tools.analyze_text import ItemLabel, save_stances

    saved = []
    ctx = SimpleNamespace(run_id="run", store=SimpleNamespace(save_stances=lambda run, rows: saved.extend(rows)))
    labels = {"i1": ItemLabel.model_validate({"id": "i1", "sentiment": "mixed", "targets": [
        {"entity": "acme", "stance": "Positive"}, {"entity": "Globex", "stance": "negative"}, {"entity": "Initech", "stance": "neutral"},
        {"entity": "Acme", "stance": "furious"}]})}
    save_stances(ctx, labels, ["Acme", "Globex"])
    assert saved == [("i1", "Acme", "positive"), ("i1", "Globex", "negative")]


def test_order_independence_returns_passage_with_figure():
    reg = agent.get_registry()
    defn = reg.get("price_per_tier")
    page = "The plan price per user is $45. " + "Lots of general text about software products and tools. " * 200 + "More pricing and plans discussion."
    ev = Evidence(evidence_id="e1", source_type=SourceType.PRICING_PAGE, source_url="https://www.globex.com/pricing",
                  extracted_fact="Pricing", excerpt=page[:1000], page_text=page)
    windows = agent.windows_for(defn, ["Globex"], [ev])
    assert any("$45" in chunk for _, chunk in windows)


def test_common_word_flood_retains_figure_sentence_and_caps_chunk_size():
    reg = agent.get_registry()
    defn = reg.get("market_size")
    page = "market " * 500 + "The global market size reached $12 billion in 2025."
    ev = Evidence(evidence_id="e1", source_type=SourceType.WEB_PAGE, source_url="https://analysts.example.com/report",
                  extracted_fact="Report", excerpt=page[:1000], page_text=page, metadata={"competitor": "Acme"})
    windows = agent.windows_for(defn, ["Acme"], [ev])
    assert any("$12 billion" in chunk for _, chunk in windows)
    assert all(len(chunk) <= 2 * agent.WINDOW for _, chunk in windows)


def test_placeholders_are_filled_and_no_braces_remain():
    reg = agent.get_registry()
    defn = reg.get("market_size")
    req = MetricRequirement(metric_id="market_size", entity="Acme",
                            query_hints=["{market} {offering} market size", "{entity} {unfilled_param} market"])
    queries = agent._queries(req, defn, offering="Cloud AI", attempt=1, market="Europe")
    assert queries
    for q in queries:
        assert "{" not in q and "}" not in q
    assert "Europe Cloud AI market size" in queries


def test_bare_hints_are_prefixed_with_entity():
    reg = agent.get_registry()
    defn = reg.get("capacity")
    custom_entity = "SyntheticCorp"
    req = MetricRequirement(metric_id="capacity", entity=custom_entity, query_hints=["capacity plan", "headcount"])
    queries = agent._queries(req, defn, offering="Ops", attempt=1)
    assert queries
    assert all(q.startswith(custom_entity) for q in queries)
    assert f"{custom_entity} capacity plan" in queries


def test_hunt_snippet_fallback():
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1)

    seen_prompts = []

    def find(prompt):
        seen_prompts.append(prompt)
        if "$99" in prompt:
            eid = prompt[prompt.rindex("[") + 1: prompt.rindex("]")]
            return Candidates(metrics=[Candidate(metric_id="price_per_tier", entity="Globex", evidence_id=eid, source_span="costs $99 per user")])
        return Candidates(metrics=[])

    search_res = [{"url": "https://www.globex.com/paywalled", "title": "Globex Pricing", "snippet": "The Enterprise plan costs $99 per user per month."}]
    agent.use(find=find, search=lambda q: search_res, fetch=lambda u: "")
    fixed, _ = run(_request(), [task], [res])

    snippet_evs = [e for e in fixed[0].evidence if e.metadata.get("snippet_only")]
    assert len(snippet_evs) == 1
    assert any("$99" in p for p in seen_prompts)
    m = fixed[0].metadata["metrics"][0]
    assert m["value"] == 99.0 and m["status"] == "verified"


def test_termination_when_all_searches_return_nothing():
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1)
    agent.use(find=lambda p: Candidates(metrics=[]), search=lambda q: [], fetch=lambda u: "")
    fixed, _ = run(_request(), [task], [res])
    out = fixed[0].metadata["requirement_outcomes"][0]
    assert out["status"] == "gap"
    assert out["attempts"] == agent.MAX_HUNTS + 1


def test_no_regression_official_source_required_remains_gap_for_third_party():
    task = _task(WorkerType.COMPETITOR_INTELLIGENCE, [_price("Globex")], "t1")
    ev = Evidence(evidence_id="e1", source_type=SourceType.NEWS_ARTICLE, source_url="https://thirdparty.example.com/pricing-review",
                  extracted_fact="Globex costs $49", excerpt="Globex pricing review. Globex costs $49 per user.",
                  page_text="Globex pricing review. Globex costs $49 per user.")
    res = WorkerResult(task_id="t1", worker=task.worker, status=WorkerStatus.COMPLETED, attempt=1, evidence=[ev])
    agent.use(find=_finder_for("Globex costs $49 per user", "Globex", "e1"), search=lambda q: [], fetch=lambda u: "")
    fixed, _ = run(_request(), [task], [res])
    out = fixed[0].metadata["requirement_outcomes"][0]
    assert out["status"] == "gap"

