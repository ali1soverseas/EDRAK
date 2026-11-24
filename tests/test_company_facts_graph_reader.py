"""Reading approved company facts back out of the graph as Evidence. A fake connection returns canned rows."""

import json

import pytest
from edrak.contracts.evidence import Evidence, SourceType
from edrak.graph.ingestion.company_facts import CompanyFactRecord, publish_facts
from edrak.graph.retrieval.graph_retriever import Q_COMPANY_FACTS, GraphRetriever

WS = "workspace-a"


class FakeConnection:
    def __init__(self, rows=None, enabled=True, error=None):
        self.rows, self.enabled, self.error = rows or [], enabled, error
        self.queries = []

    def read(self, query, **params):
        self.queries.append((query, params))
        if self.error:
            raise self.error
        return self.rows


def node(**overrides):
    base = {
        "id": "node-1",
        "fact_id": "f1",
        "workspace_id": WS,
        "category": "leadership",
        "key": "ceo",
        "value": "Amal Nasr",
        "value_type": "text",
        "quote": "Amal Nasr, Chief Executive Officer",
        "source_url": "https://acme.example/about",
        "pdf_page": None,
        "source_tier": "own_site",
        "confidence": "high",
        "edited_by_user": False,
        "conflict_group": None,
        "observed_at": "2026-11-20T10:15:30.123Z",
        "source": "company_research",
    }
    base.update(overrides)
    return {"f": base}


def facts(rows, **kwargs):
    return GraphRetriever(FakeConnection(rows)).company_facts(WS, **kwargs)


def test_a_fact_comes_back_as_evidence_with_its_quote_and_source():
    (ev,) = facts([node()])
    assert isinstance(ev, Evidence)
    assert ev.extracted_fact == "ceo: Amal Nasr"
    assert ev.excerpt == "Amal Nasr, Chief Executive Officer"
    assert ev.source_url == "https://acme.example/about" and ev.publisher == "acme.example"
    assert ev.source_title == "Graph RAG · Company profile · leadership"
    assert ev.evidence_id == "company_fact:f1"
    assert ev.is_synthetic is False and ev.retrieved_at.year == 2026


def test_the_evidence_says_a_person_approved_it_and_what_it_was_approved_against():
    (ev,) = facts([node(pdf_page=12, source_tier="own_pdf", edited_by_user=True, conflict_group="g1")])
    meta = ev.metadata
    assert meta["human_reviewed"] is True and meta["retrieval_source"] == "graph"
    assert (meta["category"], meta["key"], meta["confidence"]) == ("leadership", "ceo", "high")
    assert (meta["source_tier"], meta["pdf_page"], meta["edited_by_user"], meta["conflict_group"]) == ("own_pdf", 12, True, "g1")
    assert meta["company_fact_id"] == "f1"


def test_a_pdf_source_is_documentation_and_a_page_is_a_web_page():
    pdf, page = facts([node(source_tier="own_pdf"), node(fact_id="f2", source_tier="own_site")])
    assert pdf.source_type is SourceType.OFFICIAL_DOCUMENTATION and page.source_type is SourceType.WEB_PAGE


def test_a_list_value_reads_as_a_sentence():
    (ev,) = facts([node(key="country_presence", category="locations", value=json.dumps(["Egypt", "UAE"]), value_type="list")])
    assert ev.extracted_fact == "country presence: Egypt, UAE"


def test_a_row_with_no_value_is_skipped_rather_than_failing_the_rest():
    out = facts([node(value="  "), node(fact_id="f2", value="Mona Said", key="cfo")])
    assert [e.evidence_id for e in out] == ["company_fact:f2"]


def test_the_query_is_scoped_to_the_workspace_and_filters_by_category_and_key():
    conn = FakeConnection([node()])
    GraphRetriever(conn).company_facts(WS, category="leadership", key="ceo", limit=5)
    query, params = conn.queries[0]
    assert query == Q_COMPANY_FACTS and "workspace_id: $ws" in query
    assert params == {"ws": WS, "category": "leadership", "key": "ceo", "limit": 5}
    # passing the parameters is not enough: the query has to apply them, and still allow "any" when they are null
    assert "($category IS NULL OR f.category = $category)" in query
    assert "($key IS NULL OR f.key = $key)" in query
    assert "LIMIT $limit" in query


def test_company_facts_skip_the_freshness_and_verification_filters_of_findings():
    # a person approving a fact is the verification, and a chief executive does not age out
    for forbidden in ("verification_status", "verified_only", "since_iso", "observed_at >="):
        assert forbidden not in Q_COMPANY_FACTS


@pytest.mark.parametrize("workspace", [None, ""])
def test_without_a_workspace_nothing_is_read(workspace):
    conn = FakeConnection([node()])
    assert GraphRetriever(conn).company_facts(workspace) == []
    assert conn.queries == []  # a lost id must never widen a search to every company


def test_a_disabled_graph_returns_nothing():
    conn = FakeConnection([node()], enabled=False)
    assert GraphRetriever(conn).company_facts(WS) == [] and conn.queries == []


def test_a_broken_query_returns_nothing_unless_strict():
    assert GraphRetriever(FakeConnection(error=RuntimeError("boom"))).company_facts(WS) == []
    with pytest.raises(RuntimeError):
        GraphRetriever(FakeConnection(error=RuntimeError("boom")), strict=True).company_facts(WS)


def test_what_the_writer_saves_is_what_the_reader_expects():
    """The writer's node properties must be readable as Evidence, so the two cannot drift apart."""

    class Recorder:
        def __init__(self):
            self.calls = []

        def write(self, query, **params):
            self.calls.append((query, params))

        def write_batches(self, query, rows, **params):
            self.calls.append((query, {**params, "rows": list(rows)}))

    record = CompanyFactRecord(
        fact_id="f9", category="financials", key="annual_revenue", value="EGP 28.4 billion", value_type="number",
        quote="Revenue reached EGP 28.4 billion", source_url="https://acme.example/report.pdf", pdf_page=44,
        source_tier="own_pdf", confidence="medium", observed_at="2026-11-20T10:15:30.123Z",
    )
    writer = Recorder()
    publish_facts(writer, WS, "Acme", [record])
    props = next(p["rows"][0]["props"] for q, p in writer.calls if "MERGE (f:CompanyFact" in q)

    (ev,) = facts([{"f": props}])
    assert ev.extracted_fact == "annual revenue: EGP 28.4 billion"
    assert ev.excerpt == record.quote and ev.source_url == record.source_url
    assert ev.metadata["pdf_page"] == 44 and ev.metadata["source_tier"] == "own_pdf"
    assert ev.source_type is SourceType.OFFICIAL_DOCUMENTATION
