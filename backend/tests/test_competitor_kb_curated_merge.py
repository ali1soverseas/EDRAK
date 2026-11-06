from edrak.knowledge.competitor_kb import merge_verified_profile


def _previous():
    return {
        "curated_keys": ["ai_accelerator_intelligence", "plans", "pricing_model", "aspects"],
        "ai_accelerator_intelligence": {"scope_note": "kept"},
        "pricing_model": {"type": "quote", "summary": "old"},
        "plans": [{"name": "Enterprise", "price": "old", "curated": True},
                  {"name": "Pro", "price": "old", "curated": True}],
        "aspects": {"financials": {"label": "F", "items": [], "facts": [
            {"item": "total_revenue", "statement": "old", "mock": True, "source_url": "x", "quote": "q"},
            {"item": "margins", "statement": "old margin", "mock": True, "source_url": "x", "quote": "q"}]}},
    }


def test_rebuild_keeps_curated_data_and_replaces_only_conflicts():
    new = {
        "plans": [{"name": "Enterprise", "price": "new"}],
        "pricing_model": {"type": "tiered", "summary": "new"},
        "aspects": {"financials": {"label": "F", "items": [{"id": "total_revenue", "label": "t"}],
                                   "facts": [{"item": "total_revenue", "statement": "new"}]}},
    }
    out = merge_verified_profile(new, _previous(), [])
    assert out["ai_accelerator_intelligence"] == {"scope_note": "kept"}
    assert out["curated_keys"]
    plans = {p["name"]: p["price"] for p in out["plans"]}
    assert plans == {"Enterprise": "new", "Pro": "old"}
    assert out["pricing_model"]["summary"] == "new"
    facts = {f["item"]: f["statement"] for f in out["aspects"]["financials"]["facts"]}
    assert facts == {"total_revenue": "new", "margins": "old margin"}


def test_rebuild_restores_curated_pricing_model_when_build_finds_none():
    out = merge_verified_profile({}, _previous(), [])
    assert out["pricing_model"]["summary"] == "old"
