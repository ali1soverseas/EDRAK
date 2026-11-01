from pathlib import Path

from edrak.company_profile.url_import.discover import (
    ABOUT,
    candidates_from_links,
    candidates_from_urls,
    parse_sitemap,
    score_url,
    select_pages,
    url_language,
)
from edrak.company_profile.url_import.parse import parse_page, social_profiles

FIXTURES = Path(__file__).parent / "fixtures" / "company_sites"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def picked(html_name, base, limit=7, extra=()):
    info = parse_page(fixture(html_name), base)
    found = candidates_from_links(info.links, home_url=base) + list(extra)
    return info, select_pages(found, limit)


# ------------------------------------------------------------------ English site


def test_english_site_picks_the_high_value_pages_and_drops_the_rest():
    _, chosen = picked("en_home.html", "https://acme.example/")
    urls = [c.url for c in chosen]
    assert urls == [
        "https://acme.example/about",
        "https://acme.example/products",
        "https://acme.example/pricing",
        "https://acme.example/contact",
        "https://acme.example/about/team",
        "https://acme.example/products/terminal-x1",
    ]
    for junk in ("/blog", "/careers", "/login", "/privacy", "brochure.pdf", "how-we-grew", "senior-engineer", "other.example"):
        assert not any(junk in u for u in urls), junk
    assert all("utm_" not in u for u in urls)  # tracking is gone from the link


def test_scores_follow_path_and_link_text_and_depth():
    assert score_url("https://x.example/about")[0] > score_url("https://x.example/about/team")[0] > 0
    assert score_url("https://x.example/about")[1] == ABOUT
    assert score_url("https://x.example/x", text="About us")[0] > 0  # the link text alone is enough
    assert score_url("https://x.example/about", in_nav=True)[0] == score_url("https://x.example/about")[0] + 1
    assert score_url("https://x.example/accompany")[0] == 0  # whole words only
    for url in (
        "https://x.example/blog/2024/05/post",
        "https://x.example/news/launch",
        "https://x.example/careers/engineer",
        "https://x.example/privacy-policy",
        "https://x.example/terms",
        "https://x.example/about.pdf",
        "https://x.example/about?id=4",
        "https://x.example/cart",
        "https://x.example/login",
    ):
        assert score_url(url)[0] == 0, url
    assert score_url("https://x.example/about?lang=ar")[0] > 0


def test_a_keyword_buried_in_a_long_or_numbered_slug_is_not_a_section_page():
    # chapters and articles that merely contain the word
    assert score_url("https://x.example/gettingreal/01.2-about-basecamp")[0] == 0
    assert score_url("https://x.example/gettingreal/05.6-human-solutions")[0] == 0
    assert score_url("https://x.example/how-we-think-about-our-customers-and-their-needs")[0] == 0
    # real section pages, in the shapes sites use
    for url in ("https://x.example/about-us", "https://x.example/who-we-are", "https://x.example/about-the-company",
                "https://x.example/about.html", "https://x.example/en/products-and-services", "https://x.example/our_solutions"):
        assert score_url(url)[0] > 0, url


# ------------------------------------------------------------------ Arabic site


def test_arabic_site_scores_arabic_paths_and_link_text():
    info, chosen = picked("ar_home.html", "https://fawry.example/")
    by_url = {c.url: c for c in chosen}
    assert "https://fawry.example/%D9%85%D9%86-%D9%86%D8%AD%D9%86" in by_url  # من-نحن, found by its path
    assert by_url["https://fawry.example/%D9%85%D9%86-%D9%86%D8%AD%D9%86"].section == ABOUT
    assert {"https://fawry.example/ar/services", "https://fawry.example/ar/products", "https://fawry.example/ar/contact"} <= set(by_url)
    assert not any("privacy" in u or "login" in u for u in by_url)
    assert all(c.lang == "ar" for c in chosen)


def test_arabic_keywords_match_across_spelling_variants():
    assert score_url("https://x.example/a", text="منتجاتنا")[0] > 0
    assert score_url("https://x.example/a", text="الأسعار")[0] == score_url("https://x.example/a", text="الاسعار")[0] > 0
    assert score_url("https://x.example/a", text="عن الشركة")[1] == ABOUT
    assert score_url("https://x.example/a", text="اتصل بنا")[0] > 0


# ------------------------------------------------------------------ bilingual site


def test_bilingual_site_prefers_english_pages_and_adds_the_arabic_about_page():
    base = "https://nileledger.example/"
    en_info = parse_page(fixture("bilingual_home.html"), base)
    ar_info = parse_page(fixture("bilingual_ar_home.html"), "https://nileledger.example/ar/")
    found = candidates_from_links(en_info.links, home_url=base, home_lang="en") + candidates_from_links(
        ar_info.links, home_url=base, home_lang="ar"
    )
    chosen = select_pages(found, 7)
    urls = [c.url for c in chosen]
    assert "https://nileledger.example/about" in urls and "https://nileledger.example/services" in urls
    assert "https://nileledger.example/ar/about-us" in urls  # the Arabic about page
    assert "https://nileledger.example/ar/services" not in urls  # but English wins the other sections
    assert "https://nileledger.example/ar/contact" not in urls
    assert len(urls) == 3


def test_arabic_about_page_keeps_its_slot_when_the_limit_is_tight():
    base = "https://nileledger.example/"
    en = parse_page(fixture("bilingual_home.html"), base)
    ar = parse_page(fixture("bilingual_ar_home.html"), "https://nileledger.example/ar/")
    found = candidates_from_links(en.links, home_url=base, home_lang="en") + candidates_from_links(ar.links, home_url=base, home_lang="ar")
    chosen = select_pages(found, 2)
    assert len(chosen) == 2
    assert any(c.lang == "ar" and c.section == ABOUT for c in chosen)


def test_the_page_language_comes_from_the_url_or_the_link_text():
    assert url_language("https://x.example/ar/about") == "ar"
    assert url_language("https://ar.x.example/about") == "ar"
    assert url_language("https://x.example/en-us/about") == "en"
    assert url_language("https://x.example/about?lang=ar") == "ar"
    assert url_language("https://x.example/%D9%85%D9%86-%D9%86%D8%AD%D9%86") == "ar"
    assert url_language("https://x.example/about") is None


def test_the_other_language_home_is_found_from_hreflang():
    from edrak.company_profile.url_import.importer import _other_language_home

    info = parse_page(fixture("bilingual_home.html"), "https://nileledger.example/")
    assert info.alternates["ar"] == "https://nileledger.example/ar/"
    assert _other_language_home(info, "https://nileledger.example/", "en") == "https://nileledger.example/ar/"
    assert _other_language_home(info, "https://nileledger.example/", "ar") == "https://nileledger.example/"


# ------------------------------------------------------------------ JSON-LD and metadata


def test_json_ld_organization_is_parsed_and_bad_blocks_are_skipped():
    info = parse_page(fixture("jsonld_home.html"), "https://delta.example/")
    assert len(info.orgs) == 1  # the WebSite node and the broken block are not organizations
    org = info.orgs[0]
    assert org.name == "Delta Freight"
    assert org.alternate_names == ["دلتا للشحن", "Delta"]
    assert org.address == "12 Nile St, Giza, EG"
    assert org.area_served == ["Egypt", "Libya"]
    assert len(org.same_as) == 5
    assert info.lang == "en" and info.title == "Delta Freight" and info.has_metadata


def test_meta_tags_are_read():
    info = parse_page(fixture("en_home.html"), "https://acme.example/")
    assert info.title.startswith("Acme Pay")
    assert info.description.startswith("Card terminals")
    assert info.site_name == "Acme Pay" and info.og_description == "Payments made simple."
    assert info.lang == "en"


def test_garbage_html_does_not_raise():
    assert not parse_page("", "https://x.example/").has_metadata
    assert parse_page("<<<>>> not html", "https://x.example/").links == []


# ------------------------------------------------------------------ sitemaps


def test_sitemap_index_and_urlset_are_parsed():
    kind, locs = parse_sitemap(fixture("sitemap_index.xml").encode())
    assert kind == "index" and locs == ["https://acme.example/sitemap-pages.xml", "https://other.example/sitemap.xml"]
    kind, locs = parse_sitemap(fixture("sitemap_pages.xml").encode())
    assert kind == "urlset" and len(locs) == 7
    assert parse_sitemap(b"not xml at all")[1] == []


def test_sitemap_candidates_are_same_site_and_filtered():
    _, locs = parse_sitemap(fixture("sitemap_pages.xml").encode())
    found = candidates_from_urls(locs, home_url="https://acme.example/")
    urls = {c.url for c in found}
    assert urls == {"https://acme.example/about", "https://acme.example/solutions", "https://acme.example/who-we-are"}


def test_a_page_found_twice_is_kept_once_with_its_link_text():
    info = parse_page(fixture("en_home.html"), "https://acme.example/")
    _, locs = parse_sitemap(fixture("sitemap_pages.xml").encode())
    found = candidates_from_links(info.links, home_url="https://acme.example/") + candidates_from_urls(locs, home_url="https://acme.example/")
    chosen = select_pages(found, 7)
    assert [c.url for c in chosen].count("https://acme.example/about") == 1
    about = next(c for c in chosen if c.url == "https://acme.example/about")
    assert about.text == "About us"


def test_at_most_two_pages_per_section_and_the_limit_is_respected():
    from edrak.company_profile.url_import.discover import Candidate, identity

    many = [Candidate(f"https://x.example/products-{i}", "", False, None, 4, "products", identity(f"https://x.example/products-{i}")) for i in range(6)]
    assert len(select_pages(many, 7)) == 2
    mixed = many + [Candidate("https://x.example/about", "", False, None, 5, ABOUT, identity("https://x.example/about"))]
    assert len(select_pages(mixed, 2)) == 2
    assert select_pages(mixed, 0) == []


# ------------------------------------------------------------------ social links


def test_social_profiles_are_recognized_normalized_and_deduped():
    info = parse_page(fixture("en_home.html"), "https://acme.example/")
    found = social_profiles(link.url for link in info.links)
    assert found == [
        "https://www.facebook.com/acmepay",
        "https://x.com/acmepay",
        "https://www.linkedin.com/company/acmepay",
    ]


def test_social_profiles_cover_all_six_platforms_and_skip_non_profiles():
    found = social_profiles(
        [
            "https://facebook.com/acme", "https://m.facebook.com/acme/", "https://instagram.com/acme/",
            "https://twitter.com/acme", "https://x.com/acme", "https://www.linkedin.com/company/acme/about",
            "https://www.youtube.com/@acme", "https://www.youtube.com/watch?v=abc", "https://www.tiktok.com/@acme",
            "https://www.instagram.com/p/xyz/", "https://x.com/intent/tweet", "https://example.com/acme",
        ]
    )
    assert found == [
        "https://www.facebook.com/acme", "https://www.instagram.com/acme", "https://x.com/acme",
        "https://www.linkedin.com/company/acme", "https://www.youtube.com/@acme", "https://www.tiktok.com/@acme",
    ]


def test_same_as_urls_from_json_ld_are_recognized():
    org = parse_page(fixture("jsonld_home.html"), "https://delta.example/").orgs[0]
    assert social_profiles(org.same_as) == [
        "https://www.facebook.com/deltafreight",
        "https://x.com/deltafreight",
        "https://www.linkedin.com/company/deltafreight",
        "https://www.tiktok.com/@deltafreight",
    ]
