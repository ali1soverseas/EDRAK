import asyncio
import tempfile
from datetime import date

import pdf_fixtures as pdfs  # skips this module when PyMuPDF is not installed
import pytest
from crawl_fixtures import Clock, FakeBrowser, limits
from pdf_site_fixtures import (
    CDN,
    HOME,
    NO_OCR,
    PRIVATE_HOST,
    Files,
    crawl_and_read,
    crawl_of,
    home,
    outcomes,
    pdf_limits,
    served,
    website,
)

from edrak.company_profile.pdf_import.errors import PdfRejected
from edrak.company_profile.pdf_import.extract import extract_document
from edrak.company_research.crawler.pdf_links import embedded_pdf_sources, has_pdf_extension, viewer_target
from edrak.company_research.pdfs import Outcome, PdfCandidate, Via, find_pdfs, read_pdfs
from edrak.company_research.pdfs.candidates import (
    allowed_source,
    file_name,
    is_download_link,
    latest_year,
    rank_candidates,
    score_candidate,
)
from edrak.core.config import settings

REPORT = "https://acme.example/files/report.pdf"


@pytest.fixture
def files(tmp_path):
    return Files(tmp_path)


def by_url(result) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for doc in result.documents:
        grouped.setdefault(doc.source_url, []).append(doc)
    return grouped


# ------------------------------------------------------------------ finding PDFs


def test_a_link_to_a_pdf_file_is_found_downloaded_and_read_page_by_page(files):
    site = website('<a href="/files/report.pdf">Company overview</a>', {"/files/report.pdf": files.single()})
    crawled, result = crawl_and_read(site)

    assert outcomes(result) == {REPORT: "read"}
    assert [(d.source_url, d.pdf_page, d.title) for d in result.documents] == [
        (REPORT, 1, "About Acme Pay"),
        (REPORT, 2, "Products"),
    ]
    assert "card payment terminals" in result.documents[0].text
    assert "Acme Terminal" in result.documents[1].text
    assert result.documents[0].found_on == HOME
    report = result.reports[0]
    assert (report.via, report.pages_total, report.pages_kept) == (Via.EXTENSION, 2, 2)
    assert (result.stats.read, result.stats.pages_kept, result.stats.candidates) == (1, 2, 1)
    assert result.warnings == []


def test_a_link_whose_response_is_a_pdf_is_found_though_its_address_says_nothing(files):
    site = website(
        '<a href="/get?id=7">Download our brochure</a> <a href="/get?id=8">Download the price notice</a>',
        {"/get?id=7": files.single()},
    )
    site.pages["/get?id=8"] = (200, "<html>no file here</html>", "text/html")
    _, result = crawl_and_read(site)

    assert outcomes(result) == {"https://acme.example/get?id=7": "read", "https://acme.example/get?id=8": "not_pdf"}
    assert {r.via for r in result.reports} == {Via.DOWNLOAD}
    assert len(result.documents) == 2
    assert any("acme.example/get" in w and "not a PDF" in w for w in result.warnings)
    assert not any("id=" in w for w in result.warnings)  # a query can hold a token, so warnings leave it out


def test_a_file_served_as_a_generic_download_is_accepted_when_it_starts_like_a_pdf(files):
    site = website('<a href="/dl/profile">Company profile</a>', {"/dl/profile": (200, files.single(), "application/octet-stream")})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://acme.example/dl/profile": "read"}


@pytest.mark.parametrize(
    "text",
    ["Download", "Company profile", "Brochure", "Annual report", "تحميل", "البروشور", "الملف التعريفي", "التقرير السنوي", "Download brochure"],
)
def test_download_buttons_and_links_are_recognised_by_their_words(text):
    assert is_download_link("https://acme.example/get/1", text)


@pytest.mark.parametrize(
    ("url", "text"),
    [
        ("https://acme.example/contact", "Contact us"),
        ("https://acme.example/products", "Our products"),
        ("https://acme.example/downloader-tools", "Tools"),
    ],
)
def test_other_links_are_not_treated_as_downloads(url, text):
    assert not is_download_link(url, text)


def test_a_download_link_to_a_page_the_crawl_already_read_is_not_requested_again(files):
    site = website(
        '<a href="/downloads">Downloads</a> <a href="/downloads">Download area</a>',
        downloads=None,
    )
    site.pages["/downloads"] = (
        "<html><head><title>Downloads</title></head><body><main><h1>Downloads</h1><p>" + "Documents for you. " * 30 + "</p></main></body></html>"
    )
    _, result = crawl_and_read(site)
    assert site.paths.count("/downloads") == 1  # read once as a page, not probed again as a file
    assert result.stats.candidates == 0


@pytest.mark.parametrize(
    "markup",
    [
        '<iframe src="/files/report.pdf" title="Report"></iframe>',
        '<embed src="/files/report.pdf" type="application/pdf">',
        '<object data="/files/report.pdf" type="application/pdf"></object>',
        '<iframe data-src="/files/report.pdf"></iframe>',
        '<embed src="/files/report" type="application/pdf">',  # the type says it, the name does not
    ],
)
def test_pdfs_in_iframe_embed_and_object_elements_are_found(files, markup):
    served_path = "/files/report" if 'src="/files/report"' in markup else "/files/report.pdf"
    site = website(markup, {served_path: files.single()})
    crawled, result = crawl_and_read(site)

    assert [(p.url, p.via) for p in crawled.pdf_links] == [(f"https://acme.example{served_path}", "embed")]
    assert outcomes(result) == {f"https://acme.example{served_path}": "read"}
    assert result.reports[0].via is Via.EMBED


def test_frames_that_do_not_show_a_pdf_are_ignored():
    html = '<iframe src="https://www.youtube.com/embed/abc"></iframe><iframe src="/map"></iframe><object data="/intro.swf"></object>'
    assert embedded_pdf_sources(html, HOME) == []


def test_a_pdf_that_only_the_browser_adds_to_the_page_is_found_too(files):
    spa = "<html><head><title>Acme</title></head><body><div id='root'></div></body></html>"
    rendered = "<html><head><title>Acme</title></head><body><main>" + "Real content. " * 40 + '<iframe src="/files/report.pdf"></iframe></main></body></html>'
    site = website("", {"/files/report.pdf": files.single()})
    site.pages["/"] = spa
    crawled, result = crawl_and_read(site, crawl_limits=limits(dynamic=True), browser=FakeBrowser({HOME: rendered}))
    assert outcomes(result) == {REPORT: "read"}


@pytest.mark.parametrize(
    ("viewer", "inner"),
    [
        ("https://acme.example/pdfjs/web/viewer.html?file=%2Ffiles%2Freport.pdf", "https://acme.example/files/report.pdf"),
        ("https://acme.example/pdf/viewer?file=/files/report", "https://acme.example/files/report"),
        ("https://acme.example/web/viewer.html?file=https%3A%2F%2Fcdn.x.example%2Fa.pdf", "https://cdn.x.example/a.pdf"),
        ("https://docs.google.com/viewer?url=https%3A%2F%2Facme.example%2Ffiles%2Freport.pdf&embedded=true", "https://acme.example/files/report.pdf"),
        ("https://docs.google.com/gview?url=https://acme.example/files/report.pdf", "https://acme.example/files/report.pdf"),
        ("https://acme.example/viewer?src=docs/a.pdf&page=3", "https://acme.example/docs/a.pdf"),
    ],
)
def test_a_viewer_page_is_unwrapped_to_the_file_it_shows(viewer, inner):
    assert viewer_target(viewer) == inner


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.example/files/report.pdf",
        "https://acme.example/products?file=1",
        "https://acme.example/viewer",
        "https://acme.example/viewer?file=javascript:alert(1)",
        "https://www.google.com/search?q=viewer",
    ],
)
def test_pages_that_are_not_viewers_or_name_no_file_give_nothing(url):
    assert viewer_target(url) is None


def test_a_viewer_link_gives_the_file_not_the_viewer(files):
    viewer = f"https://docs.google.com/viewer?url=https%3A%2F%2F{CDN}%2Fbrochure.pdf&embedded=true"
    site = website(
        f'<a href="/pdfjs/web/viewer.html?file=%2Ffiles%2Freport.pdf">Read online</a><a href="{viewer}">Brochure</a>',
        {"/files/report.pdf": files.single(), f"{CDN}/brochure.pdf": files.deck()},
    )
    crawled, result = crawl_and_read(site)

    assert outcomes(result) == {REPORT: "read", f"https://{CDN}/brochure.pdf": "read"}
    assert {r.via for r in result.reports} == {Via.VIEWER}
    assert not any(host == "docs.google.com" for host, _ in site.requests)  # the viewer page itself is never fetched
    assert not any("viewer.html" in path for _, path in site.requests)


def test_the_same_file_found_three_ways_is_one_candidate(files):
    site = website(
        '<a href="/files/report.pdf">Annual report</a><iframe src="/files/report.pdf"></iframe>'
        '<a href="/pdfjs/viewer.html?file=/files/report.pdf">Read</a>',
        {"/files/report.pdf": files.single()},
    )
    crawled, result = crawl_and_read(site)
    assert [c.url for c in find_pdfs(crawled)] == [REPORT]
    assert site.paths.count("/files/report.pdf") == 1


def test_pdfs_from_the_sitemap_are_candidates(files):
    site = website("", {"/files/annual-report-2024.pdf": files.single()})
    site.pages["/sitemap.xml"] = (
        "<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
        "<url><loc>https://acme.example/files/annual-report-2024.pdf</loc></url></urlset>"
    )
    _, result = crawl_and_read(site)
    assert [r.via for r in result.reports] == [Via.SITEMAP]
    assert outcomes(result) == {"https://acme.example/files/annual-report-2024.pdf": "read"}


def test_extension_helpers():
    assert has_pdf_extension("https://a.example/x/Report.PDF")
    assert has_pdf_extension("https://a.example/dl?name=x.pdf")
    assert not has_pdf_extension("https://a.example/pdf-guide")


# ------------------------------------------------------------------ which hosts are allowed


def test_a_cdn_the_companys_own_page_links_to_is_allowed(files):
    site = website(f'<a href="https://{CDN}/files/profile.pdf">Company profile</a>', {f"{CDN}/files/profile.pdf": files.single()})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {f"https://{CDN}/files/profile.pdf": "read"}


def test_a_subdomain_of_the_company_is_allowed(files):
    site = website('<a href="https://files.acme.example/a.pdf">Report</a>', {"files.acme.example/a.pdf": files.single()})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://files.acme.example/a.pdf": "read"}


def test_a_pdf_on_an_unrelated_host_found_by_other_means_is_rejected_without_a_request(files):
    site = website("", {"evil.example/report.pdf": files.single()})
    candidates = [
        PdfCandidate("https://evil.example/report.pdf", "Annual report", "https://thirdparty.example/post"),
        PdfCandidate("https://evil.example/report.pdf", "Annual report", ""),
        PdfCandidate("https://evil.example/report.pdf", "Annual report", "sitemap"),
    ]
    result = asyncio.run(read_pdfs(candidates, HOME, fetcher=site.fetcher(), limits=pdf_limits(), ocr=NO_OCR))

    assert [r.outcome for r in result.reports] == [Outcome.NOT_ALLOWED] * 3
    assert result.documents == [] and result.stats.attempted == 0
    assert not any(host == "evil.example" for host, _ in site.requests)
    assert result.warnings and "none of the company's pages links to" in result.warnings[0]


def test_a_link_on_an_unrelated_page_does_not_make_its_host_allowed():
    candidate = PdfCandidate("https://cdn.other.example/a.pdf", found_on="https://acme.example.evil.example/page")
    assert not allowed_source(candidate, HOME)
    assert allowed_source(PdfCandidate("https://cdn.other.example/a.pdf", found_on="https://acme.example/investors"), HOME)
    assert allowed_source(PdfCandidate("https://www.acme.example/a.pdf"), HOME)


# ------------------------------------------------------------------ SSRF and robots.txt


def test_a_pdf_link_to_a_private_address_is_refused_before_any_request(files):
    site = website(f'<a href="https://{PRIVATE_HOST}/report.pdf">Report</a>', {f"{PRIVATE_HOST}/report.pdf": files.single()})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {f"https://{PRIVATE_HOST}/report.pdf": "not_allowed"}
    assert not any(host == PRIVATE_HOST for host, _ in site.requests)
    assert "not a public one" in result.warnings[0]


def test_a_redirect_to_a_private_address_is_refused_at_that_hop(files):
    site = website(
        '<a href="/files/report.pdf">Annual report</a>',
        {"/files/report.pdf": (302, "", f"https://{PRIVATE_HOST}/secret.pdf"), f"{PRIVATE_HOST}/secret.pdf": files.single()},
    )
    _, result = crawl_and_read(site)
    assert outcomes(result) == {REPORT: "not_allowed"}
    assert not any(host == PRIVATE_HOST for host, _ in site.requests)


def test_a_redirect_from_the_company_to_a_file_host_is_followed_and_checked(files):
    site = website('<a href="/download/1">Download the annual report</a>', {"/download/1": (302, "", f"https://{CDN}/r.pdf"), f"{CDN}/r.pdf": files.single()})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://acme.example/download/1": "read"}
    assert result.documents[0].source_url == "https://acme.example/download/1"  # cited by the address the company published


def test_robots_txt_is_respected_for_pdfs(files):
    site = website('<a href="/private/plan.pdf">Plan</a><a href="/files/report.pdf">Report</a>', {"/private/plan.pdf": files.single("a"), "/files/report.pdf": files.single("b")})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://acme.example/private/plan.pdf": "robots", REPORT: "read"}
    assert "/private/plan.pdf" not in site.paths


def test_files_robots_txt_forbids_do_not_use_up_the_download_attempts(files):
    """A site links many files on a host that forbids them; the allowed files after them must still be tried."""
    forbidden = "".join(f'<a href="/private/f{i}.pdf">Annual report {i}</a>' for i in range(6))
    site = website(forbidden + '<a href="/files/report.pdf">Report</a>', {"/files/report.pdf": files.single("ok"), **{f"/private/f{i}.pdf": files.single(f"f{i}") for i in range(6)}})
    _, result = crawl_and_read(site, limits=pdf_limits(max_attempts=2))
    assert outcomes(result)[REPORT] == "read"
    assert sum(1 for o in outcomes(result).values() if o == "robots") == 6
    assert result.stats.attempted == 1 and result.stats.read == 1
    assert not any(p.startswith("/private/f") for p in site.paths)
    robots_notes = [w for w in result.warnings if "robots.txt" in w]
    assert robots_notes == ["6 PDF(s) were skipped because robots.txt does not allow them (acme.example)."]  # one note, not six


def test_requests_to_one_host_are_spaced_by_the_host_delay(files):
    site = website('<a href="/a.pdf">A</a><a href="/b.pdf">B</a>', {"/a.pdf": files.numbered(1), "/b.pdf": files.numbered(2)})
    crawled, _ = crawl_and_read(site)
    clock = Clock()
    result = asyncio.run(
        read_pdfs(
            find_pdfs(crawled), HOME, fetcher=site.fetcher(), limits=pdf_limits(host_delay_s=2.0), ocr=NO_OCR, clock=clock, sleep=clock.sleep
        )
    )
    assert result.stats.read == 2
    assert clock.sleeps and all(0 < s <= 2.0 for s in clock.sleeps)


# ------------------------------------------------------------------ limits and checks on the file


def five_reports(files, extra_html=""):
    links = "".join(f'<a href="/r{i}.pdf">Report {i}</a>' for i in range(1, 6))
    return website(links + extra_html, {f"/r{i}.pdf": files.numbered(i) for i in range(1, 6)})


def test_no_more_than_the_configured_number_of_pdfs_are_read(files):
    site = five_reports(files)
    _, result = crawl_and_read(site, limits=pdf_limits(max_pdfs=2))

    assert result.stats.read == 2 and result.stats.not_tried == 3
    assert len({d.source_url for d in result.documents}) == 2
    assert sum(1 for p in site.paths if p.endswith(".pdf")) == 2  # the rest were never requested
    assert sum(1 for r in result.reports if r.outcome is Outcome.NOT_TRIED) == 3
    assert any("3 more PDF(s) were not tried" in w for w in result.warnings)


def test_the_default_limits_come_from_config():
    from edrak.company_research.pdfs import PdfLimits

    limits_ = PdfLimits.from_settings()
    assert (limits_.max_pdfs, limits_.max_bytes, limits_.max_pages) == (8, 20 * 1024 * 1024, 60)
    assert (settings.COMPANY_RESEARCH_PDF_MAX_PDFS, settings.COMPANY_RESEARCH_PDF_MAX_MB, settings.COMPANY_RESEARCH_PDF_MAX_PAGES) == (8, 20, 60)


def test_downloads_that_fail_count_against_the_attempt_limit(files):
    links = "".join(f'<a href="/bad{i}.pdf">Bad {i}</a>' for i in range(6)) + '<a href="/good.pdf">Good</a>'
    site = website(links, {"/good.pdf": files.single()})  # the bad ones answer 404
    _, result = crawl_and_read(site, limits=pdf_limits(max_attempts=3))
    assert result.stats.attempted == 3 and result.stats.unreachable <= 3
    assert result.stats.not_tried == 4
    assert sum(1 for p in site.paths if p.endswith(".pdf")) == 3


def test_a_file_over_the_size_limit_is_skipped_with_a_warning(files):
    big = files.long(8)
    site = website('<a href="/big.pdf">Big</a><a href="/small.pdf">Small</a>', {"/big.pdf": big, "/small.pdf": files.single()})
    cap = len(big) - 1
    assert len(files.single()) < cap
    _, result = crawl_and_read(site, limits=pdf_limits(max_bytes=cap))
    assert outcomes(result) == {"https://acme.example/big.pdf": "too_large", "https://acme.example/small.pdf": "read"}
    assert any("big.pdf" in w and "MB" in w for w in result.warnings)
    assert result.stats.too_large == 1


def test_a_file_that_does_not_start_with_the_pdf_marker_is_rejected_whatever_its_type(files):
    pretending = b"<html><body>Not a pdf at all</body></html>"
    site = website('<a href="/fake.pdf">Fake</a><a href="/empty.pdf">Empty</a>', {"/fake.pdf": pretending, "/empty.pdf": b""})
    _, result = crawl_and_read(site)
    assert set(outcomes(result).values()) == {"not_pdf"}
    assert result.documents == []
    assert any("contents are not a PDF" in w for w in result.warnings)


def test_a_password_protected_pdf_is_skipped_with_a_warning_and_the_others_are_read(files):
    site = website('<a href="/locked.pdf">Locked</a><a href="/open.pdf">Open</a>', {"/locked.pdf": files.encrypted(), "/open.pdf": files.single()})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://acme.example/locked.pdf": "encrypted", "https://acme.example/open.pdf": "read"}
    assert any("locked.pdf" in w and "password protected" in w for w in result.warnings)
    assert result.stats.encrypted == 1 and result.stats.read == 1
    assert "Locked content" not in " ".join(d.text for d in result.documents)


def test_a_damaged_pdf_is_skipped_with_a_warning(files):
    site = website('<a href="/broken.pdf">Broken</a>', {"/broken.pdf": b"%PDF-1.4\nthis is not a real document"})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://acme.example/broken.pdf": "corrupt"}
    assert any("could not be read as a PDF" in w for w in result.warnings)


def test_the_same_file_under_two_addresses_is_read_once(files):
    data = files.single()
    site = website('<a href="/a.pdf">A</a><a href="/copy/a.pdf">Copy</a>', {"/a.pdf": data, "/copy/a.pdf": data})
    _, result = crawl_and_read(site)
    assert sorted(outcomes(result).values()) == ["duplicate", "read"]
    assert result.stats.read == 1 and len(result.documents) == 2


def test_a_server_error_is_a_warning_not_an_exception():
    site = website('<a href="/gone.pdf">Gone</a>', {"/gone.pdf": (500, "oops", "text/plain")})
    _, result = crawl_and_read(site)
    assert outcomes(result) == {"https://acme.example/gone.pdf": "unreachable"}
    assert result.documents == []


# ------------------------------------------------------------------ reading


def test_a_long_file_is_cut_to_the_page_limit_and_says_so(files):
    site = website('<a href="/long.pdf">Annual report</a>', {"/long.pdf": files.long(8)})
    _, result = crawl_and_read(site, limits=pdf_limits(max_pages=3))
    assert [d.pdf_page for d in result.documents] == [1, 2, 3]
    assert (result.reports[0].pages_total, result.reports[0].pages_kept) == (8, 3)
    assert any("8 pages; only the first 3 were read" in w for w in result.warnings)


def test_the_pdf_importer_still_refuses_a_long_file_without_a_page_limit(files):
    path = pdfs.long_document(files.folder / "x.pdf", settings.PROFILE_IMPORT_PDF_MAX_PAGES + 1, {})
    with pytest.raises(PdfRejected) as caught:
        extract_document(str(path), ocr=NO_OCR)
    assert caught.value.code == "too_many_pages"
    assert len(extract_document(str(path), ocr=NO_OCR, page_limit=5).pages) == 5


def test_pages_are_picked_by_the_existing_rules_when_the_text_is_too_long(files):
    keyword = {9: "About us", 10: "Our products"}
    site = website('<a href="/long.pdf">Annual report</a>', {"/long.pdf": files.long(12, keyword)})
    _, result = crawl_and_read(site, limits=pdf_limits(context_chars=4500))

    numbers = [d.pdf_page for d in result.documents]
    assert numbers == sorted(numbers) and len(numbers) < 12
    assert numbers[:3] == [1, 2, 3]  # the opening pages are always kept
    assert 9 in numbers and 10 in numbers  # then the pages whose headings name the company
    assert [d.title for d in result.documents if d.pdf_page in (9, 10)] == ["About us", "Our products"]
    assert any("left out" in w for w in result.warnings)
    assert result.reports[0].pages_kept == len(numbers) and result.reports[0].pages_total == 12


def test_a_deck_gives_one_document_per_slide_with_its_heading(files):
    site = website('<a href="/deck.pdf">Investor presentation</a>', {"/deck.pdf": files.deck()})
    _, result = crawl_and_read(site)
    assert [(d.pdf_page, d.title) for d in result.documents] == list(enumerate(["Acme Pay", "The problem", "Our products", "Markets", "Customers", "Contact"], 1))


def test_arabic_pages_are_read_in_order(files):
    site = website('<a href="/ar.pdf">الملف التعريفي</a>', {"/ar.pdf": files.arabic()})
    _, result = crawl_and_read(site)
    assert pdfs.ARABIC_SAMPLE in result.documents[0].text
    assert result.documents[0].text.index(pdfs.ARABIC_HEADING) < result.documents[0].text.index("نقدم")


def test_a_scanned_page_without_ocr_is_reported_by_the_reader(files):
    site = website('<a href="/scan.pdf">Brochure</a>', {"/scan.pdf": files.scanned()})
    _, result = crawl_and_read(site)
    assert [d.pdf_page for d in result.documents] == [1]
    assert any("scan.pdf" in w and "OCR is not available" in w for w in result.warnings)
    assert result.stats.ocr_pages == 0


def test_a_file_with_no_readable_text_is_reported_and_does_not_use_up_a_slot(files):
    import pymupdf

    blank = pymupdf.open()
    blank.new_page()
    data = blank.tobytes()
    blank.close()
    site = website('<a href="/blank.pdf">Annual report</a><a href="/a.pdf">Notes</a>', {"/blank.pdf": data, "/a.pdf": files.single()})
    _, result = crawl_and_read(site, limits=pdf_limits(max_pdfs=1))
    assert outcomes(result) == {"https://acme.example/blank.pdf": "empty", "https://acme.example/a.pdf": "read"}


def test_downloaded_files_are_read_from_temporary_files_that_are_deleted(files, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    seen: list[str] = []
    original = extract_document

    def spy(path, **kwargs):
        seen.append(path)
        assert path.startswith(str(scratch)) and open(path, "rb").read(5) == b"%PDF-"
        return original(path, **kwargs)

    monkeypatch.setattr("edrak.company_profile.pdf_import.importer.extract_document", spy)
    site = website('<a href="/a.pdf">A</a><a href="/locked.pdf">B</a><a href="/broken.pdf">C</a>', {
        "/a.pdf": files.single(), "/locked.pdf": files.encrypted(), "/broken.pdf": b"%PDF-1.4 broken",
    })
    _, result = crawl_and_read(site)

    assert len(seen) == 3 and result.stats.read == 1
    assert list(scratch.iterdir()) == []  # deleted after a good file, a locked one and a damaged one


# ------------------------------------------------------------------ priority


def candidate(url, text="", section="other", via=Via.EXTENSION, found_on="https://acme.example/x"):
    return PdfCandidate(url, text, found_on, via, section)


def order(*candidates, today=date(2026, 6, 1)):
    return [c.url for c in rank_candidates(candidates, HOME, today)]


def test_link_text_and_file_name_decide_the_order():
    assert order(
        candidate("https://acme.example/a/terms-and-conditions.pdf", "Terms"),
        candidate("https://acme.example/a/x.pdf", "Download"),
        candidate("https://acme.example/a/y.pdf", "Quarterly results"),
        candidate("https://acme.example/a/z.pdf", "Annual report"),
    ) == [
        "https://acme.example/a/z.pdf",
        "https://acme.example/a/y.pdf",
        "https://acme.example/a/x.pdf",
        "https://acme.example/a/terms-and-conditions.pdf",
    ]


def test_the_file_name_counts_when_the_link_text_says_nothing():
    assert order(candidate("https://acme.example/a/misc.pdf", "Click here"), candidate("https://acme.example/a/Acme_CompanyProfile.pdf", "Click here")) == [
        "https://acme.example/a/Acme_CompanyProfile.pdf",
        "https://acme.example/a/misc.pdf",
    ]
    assert file_name("https://acme.example/a/Acme_CompanyProfile-2024.pdf") == "Acme Company Profile 2024"


@pytest.mark.parametrize("text", ["التقرير السنوي", "الملف التعريفي للشركة", "البروشور"])
def test_arabic_document_names_count(text):
    assert score_candidate(candidate("https://acme.example/f.pdf", text), HOME, 2026)[0] > score_candidate(
        candidate("https://acme.example/f.pdf", "هنا"), HOME, 2026
    )[0]


def test_the_page_a_file_was_found_on_decides_the_order_of_otherwise_equal_files():
    shuffled = ["about", "other", "media", "resources", "investors"]
    got = order(*[candidate(f"https://acme.example/{s}.pdf", "Report", s) for s in shuffled])
    names = [u.rsplit("/", 1)[-1].removesuffix(".pdf") for u in got]
    assert names[:2] == ["investors", "resources"]
    assert set(names[2:4]) == {"media", "about"}
    assert names[4] == "other"


def test_a_year_in_the_name_breaks_a_tie_with_the_latest_first():
    got = order(
        candidate("https://acme.example/annual-report-2021.pdf", "Annual report"),
        candidate("https://acme.example/annual-report-2024.pdf", "Annual report"),
        candidate("https://acme.example/annual-report.pdf", "Annual report"),
        candidate("https://acme.example/annual-report-2023.pdf", "Annual report"),
    )
    assert got == [
        "https://acme.example/annual-report-2024.pdf",
        "https://acme.example/annual-report-2023.pdf",
        "https://acme.example/annual-report-2021.pdf",
        "https://acme.example/annual-report.pdf",
    ]


def test_a_better_description_beats_a_newer_year():
    got = order(
        candidate("https://acme.example/misc-2025.pdf", "Notes"),
        candidate("https://acme.example/annual-report-2019.pdf", "Annual report"),
    )
    assert got[0].endswith("annual-report-2019.pdf")


@pytest.mark.parametrize(
    ("text", "year"),
    [("annual-report-2023", 2023), ("FY2022 results 2021", 2022), ("report 1895", 0), ("ref 20245", 0), ("2031 plan", 0), ("outlook 2027", 2027)],
)
def test_the_year_is_the_latest_one_that_is_not_in_the_future(text, year):
    assert latest_year(text, 2026) == year


def test_files_are_read_best_first(files):
    site = website(
        '<a href="/a/terms.pdf">Terms</a><a href="/a/misc.pdf">Misc</a><a href="/investors">Investors</a><a href="/a/report-2024.pdf">Annual report</a>',
        {"/a/terms.pdf": files.numbered(1), "/a/misc.pdf": files.numbered(2), "/a/report-2024.pdf": files.numbered(3)},
        investors=home('<a href="/a/results.pdf">Quarterly results</a>', "Investors"),
    )
    site.pages["/a/results.pdf"] = served(files.numbered(4))
    crawled, result = crawl_and_read(site)
    requested = [p for p in site.paths if p.startswith("/a/")]
    # A results file on the Investors page outranks an annual report linked from the homepage.
    assert requested == ["/a/results.pdf", "/a/report-2024.pdf", "/a/misc.pdf", "/a/terms.pdf"]
    assert [r.url.rsplit("/", 1)[-1] for r in result.reports] == ["results.pdf", "report-2024.pdf", "misc.pdf", "terms.pdf"]
    assert find_pdfs(crawled)[0].score >= find_pdfs(crawled)[-1].score


def test_candidates_carry_the_page_section_they_were_found_on(files):
    site = website('<a href="/investors">Investors</a>', {"/ir.pdf": files.single()}, investors=home('<a href="/ir.pdf">Report</a>', "Investors"))
    crawled, _ = crawl_and_read(site)
    assert [(c.url, c.page_section) for c in find_pdfs(crawled)] == [("https://acme.example/ir.pdf", "investors")]


def test_a_hand_built_crawl_result_works_as_input(files):
    crawled = crawl_of((REPORT, "Annual report", HOME), ("https://elsewhere.example/a.pdf", "Report", "https://elsewhere.example/page"))
    site = website("", {"/files/report.pdf": files.single(), "elsewhere.example/a.pdf": files.numbered(1)})
    from edrak.company_research.pdfs import read_company_pdfs

    result = asyncio.run(read_company_pdfs(crawled, fetcher=site.fetcher(), limits=pdf_limits(), ocr=NO_OCR))
    assert outcomes(result) == {REPORT: "read", "https://elsewhere.example/a.pdf": "not_allowed"}
