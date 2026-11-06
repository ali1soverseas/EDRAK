from edrak.agents.competitor_intelligence import discovery
from edrak.api.routers.entities import Candidate as CandidateResponse


def _hit(url, snippet, title="Broadcom XPU pricing"):
    return discovery.Hit(
        title=title,
        url=url,
        snippet=snippet,
        providers={"serper"},
        angles={"pricing"},
    )


def test_quote_finding_requires_exact_cited_evidence_from_official_site():
    hit = _hit(
        "https://broadcom.example/pricing",
        "For Broadcom XPU pricing, contact sales to request a quote.",
    )
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="custom_quote",
        pricing_status="quote_required",
        evidence_quote="contact sales to request a quote",
        source_numbers=[1],
    )
    reply = discovery.PricingFindings(findings=[finding])
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom", "website": "https://broadcom.example"},
        [hit],
        reply,
    )

    assert len(accepted) == 1
    assert accepted[0]["pricing_status"] == "quote_required"
    assert accepted[0]["source"]["official_domain_match"] is True


def test_quote_finding_is_rejected_when_quote_is_not_in_the_cited_result():
    hit = _hit("https://broadcom.example/pricing", "Contact sales for product information.")
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="custom_quote",
        pricing_status="quote_required",
        evidence_quote="contact sales to request a quote",
        source_numbers=[1],
    )
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom", "website": "https://broadcom.example"},
        [hit],
        discovery.PricingFindings(findings=[finding]),
    )

    assert accepted == []


def test_public_price_requires_specific_price_fields():
    hit = _hit("https://broadcom.example/pricing", "Broadcom XPU is $200 per accelerator monthly.")
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="per_unit",
        price="$200",
        currency="USD",
        unit="per accelerator",
        pricing_status="official_public_price",
        evidence_quote="Broadcom XPU is $200 per accelerator monthly.",
        source_numbers=[1],
    )
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom", "website": "https://broadcom.example"},
        [hit],
        discovery.PricingFindings(findings=[finding]),
    )

    assert len(accepted) == 1
    assert accepted[0]["pricing_status"] == "official_public_price"
    assert accepted[0]["source"]["official_domain_match"] is True


def test_price_amount_must_match_the_evidence_quote():
    hit = _hit("https://broadcom.example/pricing", "Broadcom XPU is $19 per accelerator.")
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="per_unit",
        price="$20",
        currency="USD",
        unit="per accelerator",
        pricing_status="official_public_price",
        evidence_quote="Broadcom XPU is $19 per accelerator.",
        source_numbers=[1],
    )
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom", "website": "https://broadcom.example"},
        [hit],
        discovery.PricingFindings(findings=[finding]),
    )

    assert accepted == []


def test_unsupported_conditions_are_not_included_in_a_price_finding():
    quote = "Broadcom XPU is $200 per accelerator monthly."
    hit = _hit("https://broadcom.example/pricing", quote)
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="per_unit",
        price="$200",
        currency="USD",
        billing_period="monthly",
        unit="per accelerator",
        pricing_status="official_public_price",
        conditions="Minimum order of 50 units.",
        evidence_quote=quote,
        source_numbers=[1],
    )
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom", "website": "https://broadcom.example"},
        [hit],
        discovery.PricingFindings(findings=[finding]),
    )

    assert accepted[0]["conditions"] is None


def test_third_party_claim_cannot_be_returned_as_official_vendor_price():
    hit = _hit(
        "https://news.example/broadcom-price",
        "Broadcom XPU sells for $200 per accelerator.",
    )
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="per_unit",
        price="$200",
        currency="USD",
        unit="per accelerator",
        pricing_status="official_public_price",
        evidence_quote="Broadcom XPU sells for $200 per accelerator.",
        source_numbers=[1],
    )
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom", "website": "https://broadcom.example"},
        [hit],
        discovery.PricingFindings(findings=[finding]),
    )

    assert accepted[0]["pricing_status"] == "reported_price"


def test_official_price_requires_a_verified_competitor_domain():
    hit = _hit("https://pricing.example/broadcom", "Broadcom XPU sells for $200 per accelerator.")
    finding = discovery.PricingFinding(
        product_or_service="Broadcom XPU",
        pricing_model="per_unit",
        price="$200",
        currency="USD",
        unit="per accelerator",
        pricing_status="official_public_price",
        evidence_quote="Broadcom XPU sells for $200 per accelerator.",
        source_numbers=[1],
    )
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False)

    accepted = service._validate_pricing_extraction(
        {"name": "Broadcom"},
        [hit],
        discovery.PricingFindings(findings=[finding]),
    )

    assert accepted[0]["pricing_status"] == "reported_price"


def test_discovery_api_response_schema_matches_nested_pricing_research():
    candidate = CandidateResponse(
        name="Broadcom",
        pricing_status="quote_required",
        pricing=[
            {
                "product_or_service": "Broadcom XPU",
                "pricing_model": "custom_quote",
                "pricing_status": "quote_required",
                "conditions": "Contact sales for a quote.",
                "evidence_quote": "Contact sales to request a quote.",
                "source": {
                    "url": "https://broadcom.example/pricing",
                    "title": "Pricing",
                    "provider": ["serper"],
                    "published_date": None,
                    "source_host": "broadcom.example",
                    "official_domain_match": True,
                },
            }
        ],
        pricing_research={
            "research_complete": True,
            "research_status": "complete",
            "rounds_attempted": 1,
            "max_rounds": 3,
            "unresolved_items": [],
        },
    )

    data = candidate.model_dump()
    assert data["pricing"][0]["source"]["provider"] == ["serper"]
    assert data["pricing_research"]["research_complete"] is True
    assert data["pricing_status"] == "quote_required"


def test_public_pricing_is_explicit_opt_in_for_non_api_callers(monkeypatch):
    received = {}

    class Service:
        def __init__(self, **kwargs):
            received.update(kwargs)

        def run(self, _ctx, limit):
            return [{"name": "Example", "limit": limit}]

    monkeypatch.setattr(discovery, "_require_model", lambda: None)
    monkeypatch.setattr(discovery, "CompetitorDiscovery", Service)

    result = discovery.discover_competitors(
        company="Company",
        include_pricing=True,
        pricing_max_rounds=2,
    )

    assert received == {"include_pricing": True, "pricing_max_rounds": 2}
    assert result == [{"name": "Example", "limit": 5}]


def test_pricing_status_collapses_findings_for_discovery_preview():
    service = discovery.CompetitorDiscovery(providers=[], use_wikidata=False, include_pricing=True)
    service.router = type("Router", (), {"providers": [object()], "calls": {}})()
    service.enrich = lambda *_args: None
    service.search = lambda *_args: [object()]
    service.candidates = lambda *_args: [object()]
    service.assess = lambda *_args: [
        {"name": "Top", "confidence": 0.8, "signals": {"results": 2}},
    ]
    service.research_pricing = lambda *_args: {
        "findings": [{"pricing_status": "reseller_price"}],
        "research_complete": False,
        "research_status": "incomplete_after_retry_limit",
        "rounds_attempted": 3,
        "max_rounds": 3,
        "queries_attempted": [],
        "unresolved_items": ["official pricing not found"],
    }

    result = service.run(discovery.DiscoveryContext(company="Example"), limit=1)

    assert result[0]["pricing_status"] == "public_price_available"
    assert result[0]["pricing_research"]["research_complete"] is False
