"""The graph writer for approved company research facts. No Neo4j: a fake connection records every query."""

import json

import pytest
from edrak.company_research.seed_keys import SEED_KEYS
from edrak.graph.entities import slugify
from edrak.graph.ingestion import company_facts as cf
from edrak.graph.ingestion.company_facts import (
    LINK_RULES,
    LINK_TYPES,
    CompanyFactRecord,
    fact_node_id,
    publish_facts,
    retract_facts,
)

WS = "workspace-a"


class Recorder:
    """Stands in for GraphConnection: keeps what was sent, in order."""

    def __init__(self):
        self.calls = []

    def write(self, query, **params):
        self.calls.append((query, params))

    def write_batches(self, query, rows, **params):
        self.calls.append((query, {**params, "rows": list(rows)}))


def fact(fact_id="f1", category="leadership", key="ceo", value="Amr Talaat", **overrides):
    data = {
        "fact_id": fact_id,
        "category": category,
        "key": key,
        "label": key,
        "value": value,
        "value_type": "text",
        "quote": f"{value} is named on the site",
        "source_url": "https://acme.example/about",
        "pdf_page": None,
        "source_tier": "own_site",
        "confidence": "high",
    }
    data.update(overrides)
    return CompanyFactRecord(**data)


def link_calls(rec):
    """(relationship, target label, rows) for every typed-link write."""
    found = []
    for query, params in rec.calls:
        for rel in LINK_TYPES:
            if f"[r:{rel} " in query:
                label = query.split("MERGE (t:")[1].split(" ")[0]
                found.append((rel, label, params["rows"]))
    return found


# ---------------------------------------------------------------------------- the rules


def test_every_link_rule_names_a_real_fact_key():
    seeded = {(spec.category, spec.key) for spec in SEED_KEYS}
    assert set(LINK_RULES) <= seeded


def test_link_types_are_the_distinct_relationships_of_the_rules():
    assert LINK_TYPES == sorted({rule.relationship for rule in LINK_RULES.values()})
    assert "OWNED_BY" not in LINK_TYPES  # already used for Metric and Decision nodes


# ---------------------------------------------------------------------------- publishing


def test_publish_writes_company_then_facts_then_links_then_sweeps():
    rec = Recorder()
    result = publish_facts(rec, WS, "Acme Industries", [fact()])

    queries = [q for q, _ in rec.calls]
    assert "MERGE (c:Company" in queries[0] and "ON CREATE SET c.name" in queries[0]
    assert rec.calls[0][1] == {"ws": WS, "company_id": "acme-industries", "company_name": "Acme Industries"}
    assert "MERGE (f:CompanyFact" in queries[1]
    assert "DELETE r" in queries[2]  # old links of this fact go before the new ones are written
    assert "LED_BY" in queries[3]
    assert "DELETE t" in queries[4]
    assert (result.facts, result.links, result.skipped_links) == (1, 1, 0)


def test_fact_node_carries_provenance_and_is_marked_as_company_research():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(pdf_page=12, source_url="https://acme.example/r.pdf", source_tier="own_pdf")])
    row = rec.calls[1][1]["rows"][0]
    props = row["props"]
    assert row["id"] == fact_node_id(WS, "f1") == props["id"]
    assert props["source"] == "company_research"
    assert (props["quote"], props["source_url"], props["pdf_page"]) == ("Amr Talaat is named on the site", "https://acme.example/r.pdf", 12)
    assert (props["source_tier"], props["confidence"], props["workspace_id"], props["fact_id"]) == ("own_pdf", "high", WS, "f1")


def test_cleared_optional_properties_are_sent_as_null_so_an_edit_leaves_nothing_stale():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(pdf_page=None, conflict_group=None)])
    props = rec.calls[1][1]["rows"][0]["props"]
    assert props["pdf_page"] is None and props["conflict_group"] is None  # SET f += map removes these


def test_node_ids_are_scoped_to_the_workspace():
    assert fact_node_id("workspace-a", "f1") != fact_node_id("workspace-b", "f1")
    assert fact_node_id("workspace-a", "f1") == fact_node_id("workspace-a", "f1")


def test_publishing_twice_sends_identical_writes():
    first, second = Recorder(), Recorder()
    facts = [fact(), fact("f2", "partners", "partner", "Orange Egypt")]
    publish_facts(first, WS, "Acme", facts)
    publish_facts(second, WS, "Acme", facts)

    def strip_time(calls):
        return [(q, {k: v for k, v in p.items() if k != "now"}) for q, p in calls]

    assert strip_time(first.calls) == strip_time(second.calls)


def test_nothing_is_written_for_no_facts():
    rec = Recorder()
    assert publish_facts(rec, WS, "Acme", []).facts == 0
    assert rec.calls == []


def test_a_company_name_without_letters_is_refused_before_any_write():
    rec = Recorder()
    with pytest.raises(ValueError):
        publish_facts(rec, WS, "  ---  ", [fact()])
    assert rec.calls == []


# ---------------------------------------------------------------------------- typed links


def test_a_leader_becomes_a_person_with_a_role():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(key="cfo", value="Mona Said")])
    rel, label, rows = link_calls(rec)[0]
    assert (rel, label) == ("LED_BY", "Person")
    assert rows == [{"fact_id": "f1", "target_id": "mona-said", "name": "Mona Said", "role": "CFO", "kind": None}]


def test_a_founder_is_linked_without_a_role():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(key="founder", value="Hassan Fathy")])
    rel, label, rows = link_calls(rec)[0]
    assert (rel, label, rows[0]["role"]) == ("FOUNDED_BY", "Person", None)


@pytest.mark.parametrize(
    "category,key,relationship",
    [
        ("ownership_and_funding", "shareholder", "HAS_SHAREHOLDER"),
        ("ownership_and_funding", "investor", "FUNDED_BY"),
        ("ownership_and_funding", "subsidiary", "HAS_SUBSIDIARY"),
        ("ownership_and_funding", "parent_company", "SUBSIDIARY_OF"),
        ("partners", "partner", "PARTNERS_WITH"),
        ("partners", "supplier", "SUPPLIED_BY"),
        ("partners", "distributor", "DISTRIBUTED_BY"),
        ("customers", "customer_name", "HAS_CUSTOMER"),
    ],
)
def test_organisation_keys_link_to_company_nodes(category, key, relationship):
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(category=category, key=key, value="Orange Egypt")])
    (rel, label, rows), = link_calls(rec)
    assert (rel, label) == (relationship, "Company")
    assert rows[0]["target_id"] == "orange-egypt"


def test_a_company_target_is_created_without_taking_ownership_of_a_shared_node():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(category="partners", key="partner", value="Nvidia")])
    query = next(q for q, _ in rec.calls if "PARTNERS_WITH" in q)
    assert "ON CREATE SET t.name = row.name" in query
    assert "t.source" not in query  # the node may belong to competitor analyses: no source mark, never swept


def test_people_and_places_are_marked_so_they_can_be_cleaned_up():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(), fact("f2", "locations", "headquarters", "Cairo, Egypt")])
    for query, params in rec.calls:
        if "MERGE (t:Person" in query or "MERGE (t:Location" in query:
            assert "t.source = $source" in query and params["source"] == "company_research"


def test_a_place_carries_its_kind_and_a_list_fans_out_to_one_link_per_item():
    rec = Recorder()
    countries = json.dumps(["Egypt", "Saudi Arabia", "UAE"])
    publish_facts(
        rec,
        WS,
        "Acme",
        [
            fact("f1", "locations", "headquarters", "Cairo, Egypt"),
            fact("f2", "locations", "country_presence", countries, value_type="list"),
        ],
    )
    (rel, label, rows), = link_calls(rec)
    assert (rel, label) == ("LOCATED_IN", "Location")
    assert [(r["fact_id"], r["target_id"], r["kind"]) for r in rows] == [
        ("f1", "cairo-egypt", "headquarters"),
        ("f2", "egypt", "country_presence"),
        ("f2", "saudi-arabia", "country_presence"),
        ("f2", "uae", "country_presence"),
    ]


def test_arabic_names_keep_their_letters_in_the_id():
    rec = Recorder()
    publish_facts(rec, WS, "Acme", [fact(key="chairman", value="محمد علي")])
    assert link_calls(rec)[0][2][0]["target_id"] == slugify("محمد علي") != ""


def test_keys_without_a_rule_are_stored_as_facts_only():
    rec = Recorder()
    result = publish_facts(
        rec, WS, "Acme", [fact("f1", "financials", "annual_revenue", "EGP 28.4 billion", value_type="number")]
    )
    assert link_calls(rec) == []
    assert result.facts == 1 and result.links == 0


def test_a_sentence_in_place_of_a_name_is_kept_as_a_fact_but_not_linked():
    rec = Recorder()
    sentence = "Mr. Amr Talaat, who has served as chief executive since the company was founded in 1990 and before that"
    result = publish_facts(rec, WS, "Acme", [fact(value=sentence)])
    assert len(sentence) > cf.MAX_NAME_CHARS
    assert link_calls(rec) == [] and result.skipped_links == 1 and result.facts == 1


def test_a_value_naming_the_company_itself_is_not_linked():
    rec = Recorder()
    result = publish_facts(rec, WS, "Acme Industries", [fact(category="ownership_and_funding", key="parent_company", value="Acme Industries")])
    assert link_calls(rec) == [] and result.skipped_links == 1


def test_a_fact_key_can_never_reach_the_query_text():
    rec = Recorder()
    hostile = "ceo]->() DETACH DELETE c //"
    publish_facts(rec, WS, "Acme", [fact(key=hostile)])
    assert all(hostile not in query for query, _ in rec.calls)
    assert link_calls(rec) == []


# ---------------------------------------------------------------------------- retracting


def test_retract_removes_links_then_facts_then_orphans_in_that_order():
    rec = Recorder()
    assert retract_facts(rec, WS, ["f1", "f2"]) == 2
    queries = [q for q, _ in rec.calls]
    assert "DELETE r" in queries[0] and "DETACH DELETE f" in queries[1] and "DELETE t" in queries[2]
    assert rec.calls[0][1] == {"fact_ids": ["f1", "f2"], "ws": WS, "types": LINK_TYPES}
    assert rec.calls[1][1] == {"node_ids": [fact_node_id(WS, "f1"), fact_node_id(WS, "f2")], "ws": WS}


def test_retract_never_removes_a_company_node():
    rec = Recorder()
    retract_facts(rec, WS, ["f1"])
    # What each query deletes: a link (r), a CompanyFact (f), or a swept entity (t). Never the Company (c).
    deleted = [query.split("DELETE ")[-1].strip() for query, _ in rec.calls]
    assert deleted == ["r", "f", "t"]
    drop_facts, sweep = rec.calls[1][0], rec.calls[2][0]
    assert "MATCH (f:CompanyFact)" in drop_facts
    assert "(t:Person OR t:Location)" in sweep and ":Company" not in sweep
    assert "t.source = $source" in sweep  # only entities created by company research are ever swept
    assert rec.calls[2][1] == {"source": "company_research"}


def test_retract_is_limited_to_the_workspace_and_to_the_link_types_it_writes():
    rec = Recorder()
    retract_facts(rec, "workspace-b", ["f1"])
    for query, params in rec.calls[:2]:
        assert params["ws"] == "workspace-b"
        assert "$ws" in query
    assert "type(r) IN $types" in rec.calls[0][0]


def test_retracting_nothing_writes_nothing():
    rec = Recorder()
    assert retract_facts(rec, WS, []) == 0 and retract_facts(rec, WS, [""]) == 0
    assert rec.calls == []
