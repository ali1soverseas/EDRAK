"""Hybrid retrieval and coverage for the competitor worker (agents/competitor_intelligence/prior.py).

No Neo4j, Chroma or model: the graph connection, the company-document search and the coverage
model call are replaced with fakes. The coverage verdicts themselves are code, and that is what
these tests pin down.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import pytest

from edrak.agents.competitor_intelligence import prior
from edrak.agents.competitor_intelligence.sources import register_official_domains


def ev(eid, url, fact, when, obs=()):
    return {"evidence_id": eid, "source_url": url, "source_title": "t " + eid, "publisher": None, "source_type": "web_page",
            "extracted_fact": fact, "excerpt": fact + " More detail on the page about the product line and its availability.",
            "retrieved_at": when, "is_synthetic": False, "observations": [dict(o) for o in obs]}


def fd(fid, statement, when, status="verified", worker="competitor_intelligence", conf=0.85, evidence=(), req="r-old"):
    return {"finding_id": fid, "statement": statement, "category": "product_feature", "worker": worker, "request_id": req,
            "verification_status": status, "confidence": conf, "evidence_quality": None, "observed_at": when,
            "synthetic_only": False, "limitations": [], "evidence": list(evidence)}


GRAPH = {
 "amd": [
  fd("f-mi400", "AMD Instinct MI400 offers 432 GB of HBM memory per accelerator.", "2026-10-15T09:00:00+00:00",
     evidence=[ev("e-mi400", "https://www.chipinsights.net/accelerators/amd-instinct-mi400", "AMD Instinct MI400 offers 432 GB of HBM memory per accelerator.", "2026-10-15T09:00:00+00:00", [{"metric_id": "hbm_capacity_gb", "entity_id": "amd", "value_text": "432 GB"}])]),
  fd("f-mi400-dup", "AMD Instinct MI400 offers 432 GB of HBM memory per accelerator.", "2026-10-01T09:00:00+00:00"),  # same statement, older run
  fd("f-mi355", "AMD Instinct MI355X offers 288 GB of HBM memory per accelerator.", "2026-09-20T09:00:00+00:00",
     evidence=[ev("e-mi355", "https://www.chipinsights.net/accelerators/amd-instinct-mi355x", "AMD Instinct MI355X offers 288 GB of HBM memory per accelerator.", "2026-09-20T09:00:00+00:00", [{"metric_id": "hbm_capacity_gb", "entity_id": "amd"}])]),
  fd("f-rocm", "Developers report AMD ROCm can port CUDA code with the HIPIFY tool.", "2026-10-15T09:00:00+00:00", status="insufficient",
     evidence=[ev("e-rocm", "https://community.example.net/rocm", "Developers report AMD ROCm can port CUDA code with the HIPIFY tool.", "2026-10-15T09:00:00+00:00")]),
  fd("f-cmp", "AI accelerators: compared with NVIDIA, AMD is broader. MI400 carries more memory.", "2026-10-15T09:00:00+00:00"),
 ],
 "broadcom": [
  fd("f-th6", "Broadcom Tomahawk 6 Ethernet switches ship for AI clusters.", "2026-07-01T09:00:00+00:00",
     evidence=[ev("e-th6", "https://www.semiwatchdaily.com/news/broadcom-tomahawk-6-ships", "Broadcom Tomahawk 6 Ethernet switches ship for AI clusters.", "2026-07-01T09:00:00+00:00")]),
 ],
 "intel-corp": [
  fd("f-gaudi", "Intel Corp prices Gaudi 3 at 15,625 dollars per accelerator.", "2026-10-15T09:00:00+00:00",
     evidence=[ev("e-gaudi", "https://www.chipinsights.net/accelerators/intel-gaudi-3", "Intel Corp prices Gaudi 3 at 15,625 dollars per accelerator.", "2026-10-15T09:00:00+00:00", [{"metric_id": "list_price", "entity_id": "intel-corp", "value_text": "15,625 dollars"}])]),
 ],
}
class FakeConn:
    def available(self): return True
    def read(self, q, **p):
        assert p["ws"] == "sandbox" and p["exclude"] == "req-now"
        return GRAPH.get(p["company_id"], [])

class Docs:
    def __init__(self): pass
    def search(self, query, ws, entity_id, exclude):
        if "custom accelerator" in query:
            return [NS(source_title="Battlecard: Broadcom", source_url="internal://battlecards/broadcom.md", excerpt="Broadcom builds custom accelerators for Google and Meta.", extracted_fact="", metadata={"relevance_score": 0.7}, is_synthetic=False)]
        if "Tomahawk" in query:  # irrelevant hit (other company) is dropped
            return [NS(source_title="Pricing sheet", source_url="internal://pricing.md", excerpt="Our list prices.", extracted_fact="", metadata={"relevance_score": 0.9}, is_synthetic=False)]
        return []

RULES = {"R1": ["432", "288"], "R2": [], "R3": ["tomahawk"], "R4": ["gaudi"], "R6": ["rocm"]}
FULL = {"R3", "R4", "R6"}
def responder(prompt):
    reqs = json.loads(prompt.split("REQUIREMENTS:")[1].split("EARLIER FINDINGS:")[0])
    cands = json.loads(prompt.split("EARLIER FINDINGS:")[1].split("RULES")[0])
    assert all(": compared with " not in c["finding"] for c in cands), "comparison rows must not be candidates"
    matches = []
    for r in reqs:
        ids = [c["id"] for c in cands if any(k in c["finding"].lower() for k in RULES.get(r["id"], []))]
        if ids: matches.append(NS(requirement_id=r["id"], prior_ids=ids + ["P99"], answers="full" if r["id"] in FULL else "partial"))
    return NS(matches=matches)



STATE = {
"today": "2026-11-08", "workspace_id": "sandbox", "parent_request_id": "req-now", "research_goal": "g",
 "competitors": ["AMD", "Broadcom", "Intel Corp"], "competitor_info": {}, "business_context": {"time_window_days": None},
 "research_requirements": [
  {"id": "R1", "competitor": "AMD", "requirement": "Memory per accelerator of AMD Instinct GPUs (hbm_capacity_gb)", "metric_id": "hbm_capacity_gb"},
  {"id": "R2", "competitor": "AMD", "requirement": "Pricing structure of AMD Instinct accelerators"},
  {"id": "R6", "competitor": "AMD", "requirement": "How AMD software supports porting CUDA code"},
  {"id": "R3", "competitor": "Broadcom", "requirement": "Ethernet switching products Broadcom sells for AI clusters"},
  {"id": "R5", "competitor": "Broadcom", "requirement": "Broadcom custom accelerator programs with hyperscalers"},
  {"id": "R4", "competitor": "Intel Corp", "requirement": "List price of Intel Gaudi 3 (list_price)", "metric_id": "list_price"},
 ], "verified_findings": [], "search_results": []}



@pytest.fixture
def state(monkeypatch):
    import edrak.graph.connection as gc

    monkeypatch.setattr(gc, "get_connection", lambda: FakeConn())
    monkeypatch.setattr(prior, "_CompanyDocuments", Docs)
    monkeypatch.setattr(prior, "call_structured", lambda prompt, model: responder(prompt))
    monkeypatch.setattr(prior, "_official_required", lambda r: r.get("metric_id") == "list_price")
    monkeypatch.setattr(prior, "save_node_output", lambda *a, **k: None)
    register_official_domains("Intel Corp", ["intel.com"])
    s = copy.deepcopy(STATE)
    s.update(prior.hybrid_retrieval_node(s))
    s.update(prior.assess_coverage_node(s))
    return s


def _cov(state):
    return {c["requirement_id"]: c for c in state["requirement_coverage"]}


def test_retrieval_reads_both_stores(state):
    assert [len(state["prior_findings"][c]) for c in state["competitors"]] == [4, 1, 1]  # repeated statement dropped
    assert list(state["internal_context"]) == ["R5"]  # the off-topic company document is dropped


def test_each_coverage_state(state):
    cov = _cov(state)
    assert cov["R1"]["status"] == "insufficient" and cov["R1"]["reused_finding_ids"] == ["f-mi400"]
    assert cov["R2"]["status"] == "missing"
    assert cov["R3"]["status"] == "stale" and cov["R3"]["last_verified"] == "2026-07-01"
    assert cov["R4"]["status"] == "insufficient"  # third-party price, official source required
    assert cov["R5"]["status"] == "unverified"  # only a company battlecard
    assert cov["R6"]["status"] == "unverified"  # judged insufficient by an earlier verification


def test_reused_findings_keep_their_original_evidence(state):
    assert {f["prior_finding_id"] for f in state["verified_findings"]} == {"f-mi400", "f-gaudi"}
    src = {s["url"]: s for s in state["search_results"]}
    mi400 = src["https://www.chipinsights.net/accelerators/amd-instinct-mi400"]
    assert mi400["stage"] == 0 and mi400["origin"] == prior.PRIOR_ORIGIN and mi400["evidence_id"] == "e-mi400"
    fields = prior.prior_evidence_fields(mi400)
    assert "evidence_id" not in fields and fields["retrieved_at"].isoformat().startswith("2026-10-15")
    assert prior.prior_evidence_metadata(mi400)["original_evidence_id"] == "e-mi400"
    assert prior.prior_evidence_fields({"url": "https://example.com"}) == {}


def test_queries_target_only_what_is_not_covered(state):
    targets = {t["requirement_id"]: t for t in prior.coverage_targets(state)}
    assert set(targets) == {"R1", "R2", "R3", "R4", "R5", "R6"}
    assert targets["R3"]["coverage"] == "stale" and targets["R3"]["earlier_facts"]


def test_stale_fact_not_reconfirmed_becomes_a_gap(state):
    state["requirement_checks"] = [{"requirement_id": "R3", "status": "missing"}]
    result = NS(metadata={}, gaps=[])
    prior.annotate_result(result, state)
    assert result.metadata["prior_reuse"]["findings_reused"] == 2
    assert len(result.gaps) == 1 and "last verified 2026-07-01" in result.gaps[0]


def test_model_failure_falls_back_to_words_and_never_covers(monkeypatch, state):
    monkeypatch.setattr(prior, "call_structured", lambda prompt, model: None)
    s = dict(state, verified_findings=[], search_results=[])
    s.update(prior.assess_coverage_node(s))
    cov = _cov(s)
    assert cov["R1"]["matched_by"] == "metric" and cov["R1"]["status"] == "insufficient"
    assert cov["R3"]["matched_by"] == "lexical"
    assert all(c["status"] != "covered" for c in s["requirement_coverage"] if c["matched_by"] == "lexical")


def test_without_graph_or_workspace_everything_is_missing(state):
    s = dict(state, workspace_id=None, verified_findings=[], search_results=[])
    s.update(prior.hybrid_retrieval_node(s))
    s.update(prior.assess_coverage_node(s))
    assert {c["status"] for c in s["requirement_coverage"]} == {"missing"} and s["verified_findings"] == []


@pytest.mark.parametrize("change", ["old", "future", "missing", "synthetic", "internal", "invalid_url", "malformed_url"])
def test_invalid_support_is_not_reused(change):
    p = copy.deepcopy(GRAPH["amd"][0])
    e = p["evidence"][0]
    if change == "old":
        e["retrieved_at"] = "2026-01-01T00:00:00Z"
    elif change == "future":
        e["retrieved_at"] = "2027-01-01T00:00:00Z"
    elif change == "missing":
        e["retrieved_at"] = None
    elif change == "synthetic":
        e["is_synthetic"] = True
    elif change == "internal":
        e["source_type"] = "internal_document"
    elif change == "malformed_url":
        e["source_url"] = "https://[broken"
    else:
        e["source_url"] = "internal://private"
    result = prior._judge(p, 30, "2026-11-08")
    assert not (result["fresh"] and result["grounded"])
    assert not result["evidence"]


def test_old_source_cannot_borrow_freshness_from_another_source():
    p = copy.deepcopy(GRAPH["amd"][0])
    p["evidence"][0]["retrieved_at"] = "2026-01-01T00:00:00Z"
    p["evidence"].append(ev("new", "https://example.net/news", "AMD announced a new product.", "2026-11-01T00:00:00Z"))
    result = prior._judge(p, 30, "2026-11-08")
    assert [e["evidence_id"] for e in result["evidence"]] == ["new"]
    assert not result["grounded"]


def test_unknown_dates_sort_without_platform_timestamp_errors():
    req = [{"id": "R1", "requirement": "AMD memory"}]
    candidates = prior._candidates({}, "AMD", req, [fd("x", "AMD memory", None)])
    assert candidates["P1"]["finding_id"] == "x"


def test_wrong_metric_entity_never_covers_on_fallback(monkeypatch):
    monkeypatch.setattr(prior, "call_structured", lambda *args: None)
    candidate = copy.deepcopy(GRAPH["amd"][0])
    candidate["evidence"][0]["observations"][0]["entity_id"] = "other-company"
    req = {"id": "R1", "requirement": "AMD memory", "metric_id": "hbm_capacity_gb"}
    mapped = prior._map_requirements({}, "AMD", [req], {"P1": candidate})
    assert mapped.get("R1", {}).get("answers") != "full"
    assert mapped.get("R1", {}).get("how") != "metric"


def test_partial_reuse_stays_open_without_fresh_finding(monkeypatch, state):
    from edrak.agents.competitor_intelligence import nodes
    monkeypatch.setattr(nodes, "save_node_output", lambda *args, **kwargs: None)
    result = nodes.check_research_requirements_node(state)
    assert "R1" in {r["requirement_id"] for r in result["missing_information"]}
    fresh = dict(state["verified_findings"][0], origin="web")
    state["verified_findings"].append(fresh)
    monkeypatch.setattr(prior, "call_structured", lambda *args: None)
    result = nodes.check_research_requirements_node(state)
    assert "R1" in {r["requirement_id"] for r in result["missing_information"]}
    monkeypatch.setattr(prior, "call_structured", lambda *args: NS(matches=[
        NS(requirement_id="R1", prior_ids=["P2"], answers="full")
    ]))
    result = nodes.check_research_requirements_node(state)
    assert "R1" not in {r["requirement_id"] for r in result["missing_information"]}


def test_full_fresh_verified_finding_can_cover(monkeypatch, state):
    monkeypatch.setattr(prior, "call_structured", lambda *args: NS(matches=[
        NS(requirement_id="R1", prior_ids=["P1"], answers="full")
    ]))
    one = dict(state, competitors=["AMD"], research_requirements=[state["research_requirements"][0]],
               prior_findings={"AMD": [GRAPH["amd"][0]]}, verified_findings=[], search_results=[])
    result = prior.assess_coverage_node(one)
    assert result["requirement_coverage"][0]["status"] == "covered"
    assert len(result["verified_findings"]) == 1


def test_fresh_results_do_not_overwrite_prior_source(monkeypatch, state):
    from edrak.agents.competitor_intelligence import nodes
    source = next(s for s in state["search_results"] if s["competitor"] == "AMD")
    monkeypatch.setattr(nodes, "save_node_output", lambda *args, **kwargs: None)
    monkeypatch.setattr(nodes, "run_tavily", lambda *args: {
        "results": [{"url": source["url"], "title": "irrelevant", "content": "unrelated"}]
    })
    state["search_queries"] = [{"competitor": "AMD", "requirement_id": "R1", "query": "AMD memory"}]
    result = nodes.search_node(state)
    assert source in result["search_results"]


def test_reused_source_creates_new_evidence_without_refreshing_date(state):
    from edrak.agents.competitor_intelligence import nodes
    state["task_id"] = "test-task"
    result = nodes.build_worker_result(state)
    reused = next(e for e in result.evidence if e.metadata.get("original_evidence_id") == "e-mi400")
    assert reused.evidence_id != "e-mi400"
    assert reused.retrieved_at.isoformat().startswith("2026-10-15")
