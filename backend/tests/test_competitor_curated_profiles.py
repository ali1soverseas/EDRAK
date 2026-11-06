from edrak.agents.competitor_intelligence import gap_nodes as g
from edrak.knowledge.competitor_kb import aspect_coverage, merge_verified_profile
from edrak.knowledge.profile_aspects import BY_ID


def _profile():
    return {
        "aspects": {"pricing_commercial": {"label": "P", "items": [{"id": "public_prices", "label": "x"}], "facts": [
            {"item": "public_prices", "statement": "No list price.", "quote": "q", "source_url": "https://a.com/p",
             "status": "verified", "as_of": "2026-11-05"},
            {"item": "quote_or_undisclosed", "statement": "Third party.", "quote": "q2", "source_url": "https://b.com/p",
             "status": "third_party_only", "as_of": "2026-11-05"}]}},
        "ai_accelerator_intelligence": {
            "scope_note": "ignored",
            "product_catalog": [{"name": "Gaudi 3", "summary": "AI accelerator.", "specifications": {"memory": "128 GB"},
                                 "source_url": "https://a.com/g3"}],
            "customers": [{"claim": "IBM Cloud offers it.", "quote": "IBM Cloud", "source_url": "https://a.com/n",
                           "source_type": "official", "status": "verified"}],
            "pricing": {"status": "not_disclosed", "notes": ["no price"]},
            "source_urls": [{"claim": "skip", "source_url": "https://x"}],
        },
    }


def test_profile_facts_carry_source_type_and_ignore_ai_accelerator_example():
    facts = g._profile_facts(_profile(), "NVIDIA")
    by_value = {f["value"]: f for f in facts}
    assert by_value["No list price."]["source_type"] == "official"
    assert by_value["Third party."]["source_type"] == "third_party"
    assert "Gaudi 3: AI accelerator." not in by_value
    assert "IBM Cloud offers it." not in by_value
    assert "skip" not in by_value
    assert all(f["label"] and f["value"] and f["quote"] and f["source_url"] and f["source_type"] for f in facts)


def test_rebuild_keeps_curated_aspect_facts_without_page_quotes():
    previous = {"curated_keys": ["aspects"], "aspects": _profile()["aspects"],
                "aspects_by_scope": {"k": _profile()["aspects"]}}
    out = merge_verified_profile({"scope": {"key": "k"}}, previous, [])
    facts = out["aspects"]["pricing_commercial"]["facts"]
    assert {f["item"] for f in facts} == {"public_prices", "quote_or_undisclosed"}


def test_mock_facts_are_not_counted_as_coverage_or_retrieved_as_evidence():
    mock = {"item": "public_prices", "statement": "Illustrative price.", "quote": "Illustrative price.",
            "source_url": "https://example.com", "status": "mock", "mock": True}
    coverage = aspect_coverage(BY_ID["pricing_commercial"], [mock])
    assert coverage["public_prices"] == "not_found"

    profile = _profile()
    profile["aspects"]["pricing_commercial"]["facts"].append(mock)
    facts = g._profile_facts(profile)
    assert "Illustrative price." not in {f["value"] for f in facts}
