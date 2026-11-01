import pdf_fixtures as fx
from edrak.company_profile.pdf_import.extract import PageText, extract_document
from edrak.company_profile.pdf_import.links import links_from_pages, urls_in
from edrak.company_profile.pdf_import.ocr import OcrStatus
from edrak.company_profile.pdf_import.select import page_document, score_page, select_pages


def page(number, text, links=()):
    return PageText(number=number, text=text, title="", links=list(links))


def _rendered(p):
    from edrak.company_profile.context import pack_context

    return len(pack_context([page_document(p)], budget=10**9).text)


def filler(n=400):
    return "A sentence that says very little. " * (n // 34)


# ------------------------------------------------------------------ sources per page


def test_each_page_becomes_a_source_with_its_page_ref_and_first_heading(tmp_path):
    out = extract_document(str(fx.english_single(tmp_path / "a.pdf")), ocr=OcrStatus(False))
    docs = [page_document(p) for p in out.pages]
    assert [(d.ref, d.title, d.source_type.value) for d in docs] == [
        ("p.1", "About Acme Pay", "pdf_page"),
        ("p.2", "Products", "pdf_page"),
    ]
    assert [d.priority for d in docs] == [1, 2]  # reading order


# ------------------------------------------------------------------ page selection


def test_everything_is_sent_when_it_fits():
    pages = [page(n, f"# Page {n}\n\nshort text") for n in range(1, 8)]
    chosen, warnings = select_pages(pages, budget=10_000)
    assert chosen == pages and warnings == []


def test_over_budget_keeps_the_first_three_pages_and_the_keyword_pages_in_page_order():
    pages = [page(n, f"# Section {n}\n\n{filler()}") for n in range(1, 21)]
    pages[11] = page(12, f"# Our Products and Services\n\n{filler()}")
    pages[16] = page(17, f"# About us\n\n{filler()}")
    pages[8] = page(9, f"# رؤيتنا ورسالتنا\n\n{filler()}")  # vision and mission, in Arabic
    chosen, warnings = select_pages(pages, budget=max(_rendered(p) for p in pages) * 6 + 2 * 5)  # room for six of the longest pages

    numbers = [p.number for p in chosen]
    assert numbers[:3] == [1, 2, 3]
    assert {9, 12, 17} <= set(numbers)  # the keyword pages made it
    assert numbers == sorted(numbers)  # and everything is in page order
    assert len(numbers) < 20
    assert len(warnings) == 1 and warnings[0].startswith("The document is longer than the size limit, so page(s) ")
    left_out = [n for n in range(1, 21) if n not in numbers]
    for n in left_out:
        assert str(n) in warnings[0] or "-" in warnings[0]


def test_the_warning_names_exactly_the_pages_left_out():
    pages = [page(n, f"# Section {n}\n\n{filler()}") for n in range(1, 11)]
    pages[6] = page(7, f"# Markets\n\n{filler()}")
    budget = _rendered(pages[0]) * 5 + 2 * 4 + 5
    chosen, warnings = select_pages(pages, budget=budget)
    kept = {p.number for p in chosen}
    assert 7 in kept
    from edrak.company_profile.pdf_import.extract import page_ranges

    assert page_ranges([p.number for p in pages if p.number not in kept]) in warnings[0]


def test_headings_outweigh_the_same_word_in_the_body():
    assert score_page("# About us\n\nfiller") > score_page("# Section\n\nWe will talk about us later")
    assert score_page("# Our products\n\nx") > score_page("# Section 4\n\nx")
    assert score_page("# من نحن\n\nنص") > 0
    assert score_page("# رؤيتنا\n\nنص") > 0 and score_page("# منتجاتنا\n\nنص") > 0
    assert score_page("# Section\n\nnothing relevant") == 0


def test_pages_that_tie_are_taken_earlier_first():
    pages = [page(n, f"# Section {n}\n\n{filler()}") for n in range(1, 9)]
    budget = _rendered(pages[0]) * 4 + 2 * 3 + 5  # room for four pages and their separators
    chosen, _ = select_pages(pages, budget=budget)
    assert [p.number for p in chosen] == [1, 2, 3, 4]


def test_the_default_budget_is_the_cores(monkeypatch):
    from edrak.core.config import settings

    pages = [page(n, f"# Section {n}\n\n{filler(2000)}") for n in range(1, 40)]
    monkeypatch.setattr(settings, "PROFILE_IMPORT_CONTEXT_CHARS", 10_000)
    chosen, warnings = select_pages(pages)
    assert len(chosen) < 39 and warnings


def test_a_long_generated_pdf_is_cut_to_the_budget_and_keeps_keyword_pages(tmp_path, monkeypatch):
    from edrak.core.config import settings

    path = fx.long_document(tmp_path / "long.pdf", 30, {14: "Who we are", 22: "Our solutions"})
    pages = extract_document(str(path), ocr=OcrStatus(False)).pages
    monkeypatch.setattr(settings, "PROFILE_IMPORT_CONTEXT_CHARS", 9_000)
    chosen, warnings = select_pages(pages)
    numbers = [p.number for p in chosen]
    assert numbers[:3] == [1, 2, 3] and {14, 22} <= set(numbers) and numbers == sorted(numbers)
    assert warnings and len(chosen) < 30


# ------------------------------------------------------------------ links in the PDF


def test_urls_are_found_in_text_and_hyperlinks_without_trailing_punctuation():
    pages = [
        page(1, "Visit https://www.acmepay.example/en, or www.acmepay.example. Mail us (https://acmepay.example/contact)!", links=["https://acmepay.example/press"]),
        page(2, "Find us on facebook.com/acmepay and instagram.com/acme.pay."),
    ]
    urls = urls_in(pages)
    assert urls[0] == "https://acmepay.example/press"  # real hyperlinks first
    assert "https://www.acmepay.example/en" in urls and "https://www.acmepay.example" in urls
    assert "https://acmepay.example/contact" in urls
    assert "https://facebook.com/acmepay" in urls and "https://instagram.com/acme.pay" in urls


def test_social_profiles_and_the_website_come_out_as_extra_links():
    pages = [
        page(1, "Our site: https://www.acmepay.example/about  Twitter: https://twitter.com/acmepay"),
        page(2, "More at www.acmepay.example. Follow https://www.linkedin.com/company/acmepay/ and linkedin.com/in/some-founder"),
        page(3, "Partner: https://partner.example/ Share https://www.facebook.com/sharer/sharer.php?u=1 Video https://youtube.com/@acmepay https://tiktok.com/@acmepay https://instagram.com/p/xyz/ https://facebook.com/acmepay https://www.facebook.com/acmepay/"),
    ]
    links = links_from_pages(pages)
    assert links[0] == {"platform": "website", "url": "https://www.acmepay.example"}  # seen three times, beats the partner
    social = [(l["platform"], l["url"]) for l in links[1:]]
    assert social == [
        ("x", "https://x.com/acmepay"),
        ("linkedin", "https://www.linkedin.com/company/acmepay"),
        ("youtube", "https://www.youtube.com/@acmepay"),
        ("tiktok", "https://www.tiktok.com/@acmepay"),
        ("facebook", "https://www.facebook.com/acmepay"),
    ]  # in the order they were printed; a share link, a post and a person's LinkedIn are not profiles


def test_no_links_in_the_pdf_means_no_extra_links():
    assert links_from_pages([page(1, "No addresses here, only words about payments.")]) == []
    only_social = links_from_pages([page(1, "https://x.com/acmepay")])
    assert [l["platform"] for l in only_social] == ["x"]  # no website invented from a social address


def test_the_core_accepts_what_the_detector_returns():
    from edrak.company_profile.draft import _clean_links

    links, warnings = _clean_links(links_from_pages([page(1, "https://www.acmepay.example https://twitter.com/acmepay")]))
    assert [(l.platform.value, l.url) for l in links] == [("website", "https://www.acmepay.example"), ("x", "https://x.com/acmepay")]
    assert warnings == []
