from edrak.agents.competitor_intelligence.prompts import queries_prompt


def test_queries_prompt_uses_target_company_name_without_website():
    prompt = queries_prompt(
        state={
            "company": "NVIDIA",
            "company_profile": {
                "name": "NVIDIA",
                "website": "https://www.nvidia.com/",
                "products": ["AI accelerators"],
            },
            "business_context": {},
        },
        stage=1,
        strategy="official_sources",
        targets=[{"requirement_id": "R1", "competitor": "Intel"}],
        known_findings=[],
        previous_searches=[],
        scope_text="- Intel: official site intel.com",
    )

    assert "TARGET COMPANY: NVIDIA" in prompt
    assert "NVIDIA" in prompt
    assert "nvidia.com" not in prompt
    assert "Do NOT include its\n  website or domain" in prompt
    assert "official site intel.com" in prompt
