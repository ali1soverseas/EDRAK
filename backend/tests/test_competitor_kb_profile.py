from datetime import date, datetime, timezone

import aiosqlite
import httpx
import pytest

from edrak.db.competitor_kb import list_pages, prune_pages
from edrak.knowledge.competitor_kb import (
    CompetitorKbBuilder,
    Fetcher,
    NewsDraft,
    Page,
    PlanDraft,
    ProfileDraft,
    SourcedValue,
    filter_recent_news,
    merge_verified_profile,
    verified_profile,
)
from edrak.agents.competitor_intelligence.discovery import DiscoveryContext


def test_search_plans_include_official_and_public_company_fact_queries():
    builder = CompetitorKbBuilder(router=object())
    context = DiscoveryContext(company="Example Corp", markets=[])

    official = [spec.text for spec in builder.profile_site_specs("example.com", context)]
    public = [spec.text for spec in builder.search_specs("Example Corp", context)]

    assert any("founded year headquarters incorporated" in query for query in official)
    assert any("annual report number of employees workforce" in query for query in official)
    assert any("customers partners distributors sales channels" in query for query in official)
    assert any("global presence locations countries markets served operations headquarters" in query for query in official)
    assert any("founded year headquarters incorporated" in query for query in public)
    assert any("annual report number of employees workforce" in query for query in public)
    assert any("customers partners distributors" in query for query in public)
    assert any("countries served locations operations" in query for query in public)
    assert any("ownership publicly traded stock exchange" in query for query in public)

    public_terms = builder.public_terms_specs(
        "Broadcom", "broadcom.com",
        DiscoveryContext(company="Broadcom", offering="AI accelerators", markets=[]),
    )
    assert len(public_terms) >= 3
    assert all("-site:broadcom.com" in query.text for query in public_terms)
    assert any("price" in query.text and "AI accelerators" in query.text for query in public_terms)
    assert any("licensing" in query.text for query in public_terms)
    assert any("reseller" in query.text for query in public_terms)


def test_search_plans_prioritize_date_relative_latest_news():
    builder = CompetitorKbBuilder(router=object())
    context = DiscoveryContext(company="Intel", offering="AI accelerators", markets=[])

    news_specs = [spec for spec in builder.search_specs("Intel", context) if spec.angle == "kb_news"]
    current_year = datetime.now(timezone.utc).year

    assert len(news_specs) == 2
    assert "latest news" in news_specs[0].text
    assert "news" in news_specs[1].text
    assert str(current_year) in news_specs[1].text
    assert str(current_year - 1) in news_specs[1].text


def test_official_product_discovery_queries_target_full_catalog():
    builder = CompetitorKbBuilder(router=object())

    queries = [spec.text for spec in builder.profile_site_specs(
        "broadcom.com",
        DiscoveryContext(company="Broadcom", offering="AI accelerators", markets=[]),
    )]

    assert any("product portfolio solutions software platform" in query for query in queries)
    assert any("product families services offerings" in query for query in queries)


def test_homepage_product_links_get_more_slots_than_other_page_types():
    builder = CompetitorKbBuilder(router=object())
    home = Page(
        url="https://example.com",
        title="Example",
        text="Example software portfolio",
        source_type="official",
        page_type="home",
        links=[
            (f"https://example.com/products/product-{i}", f"Product {i}")
            for i in range(1, 7)
        ] + [
            (f"https://example.com/about/about-{i}", f"About {i}")
            for i in range(1, 5)
        ],
    )

    targets = builder.official_targets(home, "https://example.com", DiscoveryContext(company="Example"))

    assert sum(1 for _url, label in targets if "product" in label.lower()) == 4
    assert sum(1 for _url, label in targets if "about" in label.lower()) == 2


def test_third_party_pricing_page_can_be_kept_when_price_sentence_omits_brand():
    page = Page(
        url="https://reseller.example.com/broadcom-accelerators",
        title="Broadcom AI accelerator pricing and licensing",
        text=(
            "Broadcom offers a range of custom accelerators for large AI deployments. "
            "The products are supplied through specialized enterprise sales channels. "
            "Current list prices are available by requesting a quote from authorized distributors. "
            "Final costs vary by configuration, volume, delivery region, and support requirements. "
            "Customers should contact the reseller for a current written quotation."
        ),
        source_type="third_party",
        page_type="pricing",
    )
    context = DiscoveryContext(company="Example", offering="AI accelerators", markets=[])

    keep, _score = CompetitorKbBuilder(router=object()).third_party_ok("Broadcom", page, context)

    assert keep


def test_customer_segments_require_a_direct_company_relationship():
    pages = [
        Page(
            url="https://www.broadcom.com/ai",
            title="AI",
            text=(
                "Now, healthcare institutions can harness artificial intelligence "
                "to define the future of medicine. "
                "Modern AI data centers consist of XPUs, CPUs, and GPUs."
            ),
            source_type="official",
            page_type="product",
        ),
        Page(
            url="https://www.broadcom.com/customers",
            title="Customers",
            text="Broadcom's customers include hyperscalers deploying custom AI accelerators.",
            source_type="official",
            page_type="product",
        ),
    ]
    draft = ProfileDraft(
        customer_segments=[
            SourcedValue(
                value="healthcare institutions",
                quote=(
                    "Now, healthcare institutions can harness artificial intelligence "
                    "to define the future of medicine."
                ),
                page=1,
            ),
            SourcedValue(
                value="AI data centers",
                quote="Modern AI data centers consist of XPUs, CPUs, and GPUs.",
                page=1,
            ),
            SourcedValue(
                value="hyperscalers",
                quote="Broadcom's customers include hyperscalers deploying custom AI accelerators.",
                page=2,
            ),
        ]
    )

    profile = verified_profile(draft, pages, "Broadcom")

    assert [segment["value"] for segment in profile["customer_segments"]] == ["hyperscalers"]


def test_unsupported_key_fact_draft_is_replaced_by_cited_value():
    page = Page(
        url="https://example.com/company",
        title="Company",
        text="Example Corp was founded in 1991 and is based in Berlin.",
        source_type="official",
        page_type="about",
    )
    draft = ProfileDraft(
        founded=SourcedValue(
            value="Founded in 1987",
            quote="Example Corp was founded in 1991.",
            page=1,
        )
    )

    profile = verified_profile(draft, [page], "Example Corp")

    assert profile["key_facts"]["founded"]["value"] == "1991"


def test_headquarters_is_recovered_from_a_direct_cited_statement():
    page = Page(
        url="https://www.prnewswire.com/news-releases/broadcom-update.html",
        title="Broadcom Update",
        text=(
            "Broadcom is a Delaware corporation headquartered in Palo Alto, CA. "
            "For more information, visit the company website."
        ),
        source_type="third_party",
        page_type="news",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["key_facts"]["headquarters"] == {
        "value": "Palo Alto, CA",
        "quote": "Broadcom is a Delaware corporation headquartered in Palo Alto, CA.",
        "source_url": page.url,
        "as_of": page.fetched_at[:10],
        "status": "third_party_only",
    }


def test_founding_year_is_recovered_from_a_direct_cited_statement():
    page = Page(
        url="https://www.company-histories.com/Broadcom-Corporation-Company-History.html",
        title="Broadcom Corporation — Company History",
        text="Broadcom was founded as a private company in 1991 by its founders.",
        source_type="third_party",
        page_type="about",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["key_facts"]["founded"]["value"] == "1991"
    assert profile["key_facts"]["founded"]["quote"] == page.text
    assert profile["key_facts"]["founded"]["status"] == "third_party_only"


def test_pricing_status_is_public_when_a_verified_price_is_found():
    page = Page(
        url="https://broadcom.com/accelerators/pricing",
        title="Broadcom accelerator pricing",
        text="The XPU Accelerator is priced at $12,500 per unit.",
        source_type="official",
        page_type="pricing",
    )
    draft = ProfileDraft(
        plans=[
            PlanDraft(
                name="XPU Accelerator",
                price="$12,500 per unit",
                quote="The XPU Accelerator is priced at $12,500 per unit.",
                page=1,
            )
        ]
    )

    profile = verified_profile(draft, [page], "Broadcom")

    assert profile["plans"][0]["price"] == "$12,500 per unit"
    assert profile["pricing_status"] == "public_price_available"


def test_pricing_status_is_quote_required_when_sales_requires_a_quote():
    page = Page(
        url="https://broadcom.com/accelerators/how-to-buy",
        title="Broadcom XPU how to buy",
        text="For current pricing on Broadcom XPU accelerators, contact sales to request a quote.",
        source_type="official",
        page_type="sales",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["pricing_status"] == "quote_required"


def test_rebuild_keeps_previous_profile_facts_only_when_their_source_still_supports_them():
    page = Page(
        url="https://example.com/company",
        title="Company",
        text=(
            "Example Corp was founded in 1991. "
            "Example Corp offers the Example Cloud platform."
        ),
        source_type="official",
        page_type="about",
        fetched_at="2026-11-04T00:00:00Z",
    )
    previous = {
        "key_facts": {
            "founded": {
                "value": "1991",
                "quote": "Example Corp was founded in 1991.",
                "source_url": page.url,
                "as_of": "2025-01-01",
                "status": "verified",
            },
            "headquarters": {
                "value": "Paris",
                "quote": "Example Corp is headquartered in Paris.",
                "source_url": page.url,
                "as_of": "2025-01-01",
                "status": "verified",
            },
        },
        "offerings": [{
            "official_name": "Example Cloud",
            "quote": "Example Corp offers the Example Cloud platform.",
            "doc_url": page.url,
            "as_of": "2025-01-01",
            "status": "verified",
        }],
    }

    merged = merge_verified_profile({"key_facts": {}, "offerings": []}, previous, [page])

    assert merged["key_facts"]["founded"]["value"] == "1991"
    assert merged["key_facts"]["founded"]["as_of"] == "2026-11-04"
    assert merged["key_facts"]["headquarters"] is None
    assert merged["offerings"][0]["official_name"] == "Example Cloud"
    assert merged["offerings"][0]["as_of"] == "2026-11-04"


def test_rebuild_prefers_new_profile_fact_over_a_still_supported_previous_fact():
    page = Page(
        url="https://example.com/company",
        title="Company",
        text="Example Corp was founded in 1991.",
        source_type="official",
        page_type="about",
    )
    old_fact = {
        "value": "1991",
        "quote": "Example Corp was founded in 1991.",
        "source_url": page.url,
        "as_of": "2025-01-01",
        "status": "verified",
    }
    new_fact = {
        "value": "1992",
        "quote": "Updated company records show a 1992 founding date.",
        "source_url": page.url,
        "as_of": "2026-11-04",
        "status": "verified",
    }

    merged = merge_verified_profile(
        {"key_facts": {"founded": new_fact}},
        {"key_facts": {"founded": old_fact}},
        [page],
    )

    assert merged["key_facts"]["founded"] == new_fact


def test_fetcher_marks_only_definitively_missing_pages_as_gone():
    def respond(request):
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    url = "https://example.com/old-policy"
    fetcher = Fetcher(client)
    try:
        assert fetcher.fetch(url) is None
    finally:
        client.close()

    assert fetcher.gone_urls == {url}
    assert fetcher.gone_urls == {url}

@pytest.mark.parametrize(
    ("page_status", "expected_text", "expected_removed"),
    [
        (503, "Stored company facts remain available.", False),
        (404, None, True),
        (200, "Updated company facts replace the previous page content,", False),
    ],
)
def test_rebuild_refreshes_or_retains_stored_pages_without_dropping_them_on_temporary_errors(
    page_status, expected_text, expected_removed
):
    url = "https://example.com/about"
    refreshed_html = (
        "<html><title>About</title><main>"
        "Updated company facts replace the previous page content, and this sentence "
        "is deliberately long enough to make it a useful stored source."
        "</main></html>"
    )

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        if page_status == 200:
            return httpx.Response(
                page_status,
                headers={"content-type": "text/html"},
                text=refreshed_html,
                request=request,
            )
        return httpx.Response(page_status, request=request)

    class EmptyRouter:
        def run(self, *_args, **_kwargs):
            return []

    client = httpx.Client(transport=httpx.MockTransport(respond))
    builder = CompetitorKbBuilder(
        router=EmptyRouter(),
        profiler=lambda *_args: None,
        terms_profiler=lambda *_args: None,
        extractor=None,
        client=client,
    )
    builder.identify = lambda *_args: ("https://example.com", None)
    old_page = Page(
        url=url,
        title="About",
        text="Stored company facts remain available. " * 4,
        source_type="official",
        page_type="about",
        fetched_at="2025-01-01T00:00:00Z",
        via="stored",
    )
    try:
        result = builder.build(
            "Example Corp",
            "https://example.com",
            DiscoveryContext(company="Example Corp", markets=[]),
            lambda _stage: None,
            [old_page],
        )
    finally:
        client.close()

    assert bool(result.removed_urls) is expected_removed
    if expected_text is None:
        assert result.pages == []
    elif page_status == 200:
        assert expected_text in result.pages[0].text
        assert result.pages[0].fetched_at != old_page.fetched_at
    else:
        assert result.pages[0].text == old_page.text


@pytest.mark.asyncio
async def test_stored_pages_reconstruct_in_chunk_order():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    try:
        await db.execute(
            """
            CREATE TABLE competitor_kb_chunks (
                kb_key TEXT, source_url TEXT, title TEXT, source_type TEXT, page_type TEXT, market TEXT,
                language TEXT, chunk_index INTEGER, content TEXT, fetched_at TEXT
            )
            """
        )
        await db.executemany(
            "INSERT INTO competitor_kb_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("example.com", "https://example.com/about", "About", "official", "about", "any", "en",
                 1, "Second paragraph.", "2026-01-02"),
                ("example.com", "https://example.com/about", "About", "official", "about", "any", "en",
                 0, "First paragraph.", "2026-01-01"),
            ],
        )
        await db.commit()

        pages = await list_pages(db, "example.com")
    finally:
        await db.close()

    assert pages == [{
        "url": "https://example.com/about",
        "title": "About",
        "source_type": "official",
        "page_type": "about",
        "market": "any",
        "language": "en",
        "fetched_at": "2026-01-02",
        "text": "First paragraph.\n\nSecond paragraph.",
    }]


@pytest.mark.asyncio
async def test_rebuild_prunes_only_pages_confirmed_obsolete():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("CREATE TABLE competitor_kb_chunks (kb_key TEXT, source_url TEXT)")
        await db.executemany(
            "INSERT INTO competitor_kb_chunks VALUES (?, ?)",
            [
                ("example.com", "https://example.com/current"),
                ("example.com", "https://example.com/removed"),
            ],
        )

        removed = await prune_pages(db, "example.com", ["https://example.com/removed"])
        cursor = await db.execute("SELECT source_url FROM competitor_kb_chunks WHERE kb_key = 'example.com'")
        remaining = {row["source_url"] for row in await cursor.fetchall()}
    finally:
        await db.close()

    assert removed == ["https://example.com/removed"]
    assert remaining == {"https://example.com/current"}


def test_pricing_status_finds_a_published_price_even_if_extraction_misses_it():
    page = Page(
        url="https://broadcom.com/accelerators/pricing",
        title="Broadcom accelerator pricing",
        text="Broadcom lists the XPU Accelerator at $12,500 per unit.",
        source_type="official",
        page_type="pricing",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["pricing_status"] == "public_price_available"


def test_pricing_status_is_not_found_when_price_availability_is_unclear():
    page = Page(
        url="https://broadcom.com/accelerators",
        title="Broadcom accelerators",
        text=(
            "Broadcom designs custom XPU accelerators for large AI data centers. "
            "Find an authorized reseller using the distributor locator."
        ),
        source_type="official",
        page_type="product",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["pricing_status"] == "not_found"


def test_founding_year_of_a_broadcom_partner_is_not_attributed_to_broadcom():
    page = Page(
        url="https://expert.broadcom.com/partners",
        title="Broadcom Partner Directory",
        text=(
            "Founded in 2018, Albatroz Solutions is an IT services company "
            "and a reseller partner of Broadcom. Broadcom partner Albatroz is headquartered in Tübingen, Germany."
        ),
        source_type="official",
        page_type="product",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["key_facts"]["founded"] is None
    assert profile["key_facts"]["headquarters"] is None


def test_employee_fallback_rejects_undated_historical_counts():
    page = Page(
        url="https://www.company-histories.com/Broadcom-Corporation-Company-History.html",
        title="Broadcom Corporation — Company History",
        text="Broadcom had about 320 employees.",
        source_type="third_party",
        page_type="about",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["key_facts"]["employees"] is None


def test_recent_employee_count_is_recovered_with_a_dated_source():
    page = Page(
        url="https://example.com/broadcom-annual-report",
        title="Broadcom 2025 Annual Report",
        text="Broadcom employed approximately 20,000 people worldwide.",
        source_type="third_party",
        page_type="about",
    )

    profile = verified_profile(ProfileDraft(), [page], "Broadcom")

    assert profile["key_facts"]["employees"]["value"] == "20,000"
    assert profile["key_facts"]["employees"]["status"] == "third_party_only"


def test_ownership_structure_is_a_distinct_sourced_profile_field():
    page = Page(
        url="https://investors.example.com/company",
        title="Investor Relations",
        text="Broadcom Inc. is listed on the Nasdaq Global Select Market under the symbol AVGO.",
        source_type="official",
        page_type="about",
    )
    draft = ProfileDraft(ownership=SourcedValue(
        value="NASDAQ: AVGO",
        quote=page.text,
        page=1,
    ))

    profile = verified_profile(draft, [page], "Broadcom")

    assert profile["ownership"]["value"] == "NASDAQ: AVGO"
    assert profile["parent_company"] is None


def test_current_ceo_is_preserved_with_verified_citation():
    page = Page(
        url="https://www.broadcom.com/company/about-us/executives",
        title="Broadcom Executives",
        text="Broadcom President and CEO Hock E. Tan leads the company.",
        source_type="official",
        page_type="about",
    )
    draft = ProfileDraft(ceo=SourcedValue(
        value="Hock E. Tan",
        quote=page.text,
        page=1,
    ))

    profile = verified_profile(draft, [page], "Broadcom")

    assert profile["key_facts"]["ceo"]["value"] == "Hock E. Tan"
    assert profile["key_facts"]["ceo"]["source_url"] == page.url


def test_current_ceo_is_not_inferred_from_an_unrelated_company_bio():
    page = Page(
        url="https://www.broadcom.com/company/about-us/executives",
        title="Broadcom Executives",
        text="Acme Corp CEO Alex Smith leads the company.",
        source_type="official",
        page_type="about",
    )
    draft = ProfileDraft(ceo=SourcedValue(
        value="Alex Smith",
        quote=page.text,
        page=1,
    ))

    profile = verified_profile(draft, [page], "Broadcom")

    assert profile["key_facts"]["ceo"] is None


def test_excerpt_surfaces_key_fact_sentences_beyond_the_cutoff():
    from edrak.knowledge.competitor_kb import _excerpt

    filler = "The financing will fund compute capacity across several sites. " * 120
    text = filler + "Broadcom is a Delaware corporation headquartered in Palo Alto, CA. Other text follows."

    excerpt = _excerpt(text, "Broadcom")

    assert len(excerpt) <= 3600
    assert "headquartered in Palo Alto, CA" in excerpt


def test_key_facts_about_another_company_are_rejected():
    page = Page(
        url="https://docs.example.com/report",
        title="Report",
        text="Rival Inc is a company based in Newton, Massachusetts. Founded in 2001, it sells access tools.",
        source_type="official",
        page_type="other",
    )
    draft = ProfileDraft(
        headquarters=SourcedValue(value="Newton, Massachusetts", quote="a company based in Newton, Massachusetts", page=1),
    )

    profile = verified_profile(draft, [page], "Example Corp")

    assert profile["key_facts"]["headquarters"] is None


def test_acquired_companies_are_not_listed_as_partners():
    page = Page(
        url="https://news.example.com/a",
        title="News",
        text="Broadcom acquired VMware for $61 billion. Broadcom partners with Google on custom chips.",
        source_type="third_party",
        page_type="news",
    )
    draft = ProfileDraft(partners=[
        SourcedValue(value="VMware", quote="Broadcom acquired VMware for $61 billion", page=1),
        SourcedValue(value="Google", quote="Broadcom partners with Google on custom chips", page=1),
    ])

    profile = verified_profile(draft, [page], "Broadcom")

    assert [p["value"] for p in profile["partners"]] == ["Google"]


def test_latest_moves_excludes_old_and_undated_items():
    today = date.today()
    recent_date = today.strftime("%B %d, %Y")
    old_page = Page(
        url="https://news.example.com/old",
        title="Army intelligence review",
        text="April 20, 2017: Army reviewed its intelligence needs and open-source intelligence.",
        source_type="third_party",
        page_type="news",
    )
    recent_page = Page(
        url="https://news.example.com/recent",
        title="Broadcom accelerator launch",
        text=f"{recent_date}: Broadcom launched a new accelerator platform this week.",
        source_type="third_party",
        page_type="news",
    )
    draft = ProfileDraft(recent_news=[
        NewsDraft(
            date="April 20, 2017",
            headline="Army reviewed intelligence needs",
            summary="Army reviewed its intelligence needs and open-source intelligence.",
            quote="April 20, 2017: Army reviewed its intelligence needs and open-source intelligence.",
            page=1,
        ),
        NewsDraft(
            date=recent_date,
            headline="Broadcom launched accelerator platform",
            summary="Broadcom launched a new accelerator platform this week.",
            quote=f"{recent_date}: Broadcom launched a new accelerator platform this week.",
            page=2,
        ),
        NewsDraft(
            date=None,
            headline="Undated Broadcom development",
            summary="Broadcom announced a development.",
            quote="Broadcom announced a development.",
            page=2,
        ),
    ])

    profile = verified_profile(draft, [old_page, recent_page], "Broadcom")

    assert [item["headline"] for item in profile["recent_news"]] == [
        "Broadcom launched accelerator platform",
    ]


def test_cached_latest_moves_are_filtered_by_date_and_sorted_newest_first():
    old = {"date": "April 20, 2017", "headline": "Old story"}
    newer = {"date": "October 1, 2026", "headline": "Newer story"}
    undated = {"date": None, "headline": "Undated story"}

    result = filter_recent_news([old, undated, newer], today=date(2026, 11, 4))

    assert result == [newer]


def test_profile_merge_does_not_restore_stale_news():
    page = Page(
        url="https://news.example.com/old",
        title="Army intelligence review",
        text="April 20, 2017: Army reviewed its intelligence needs and open-source intelligence.",
        source_type="third_party",
        page_type="news",
    )
    previous = {"recent_news": [{
        "date": "April 20, 2017",
        "headline": "Army intelligence review",
        "quote": "April 20, 2017: Army reviewed its intelligence needs and open-source intelligence.",
        "source_url": page.url,
    }]}

    merged = merge_verified_profile({"recent_news": []}, previous, [page])

    assert merged["recent_news"] == []
