import pytest

from edrak.company_research.crawler import rules


@pytest.mark.parametrize(
    "url, section",
    [
        ("https://acme.example/investors", "investors"),
        ("https://acme.example/investor-relations", "investors"),
        ("https://acme.example/resources", "resources"),
        ("https://acme.example/downloads", "resources"),
        ("https://acme.example/reports", "resources"),
        ("https://acme.example/annual-report", "resources"),
        ("https://acme.example/media", "media"),
        ("https://acme.example/press", "media"),
        ("https://acme.example/press-releases", "media"),
        ("https://acme.example/team", "team"),
        ("https://acme.example/leadership", "team"),
        ("https://acme.example/board", "team"),
        ("https://acme.example/history", "history"),
        ("https://acme.example/careers", "careers"),
        ("https://acme.example/branches", "locations"),
        ("https://acme.example/locations", "locations"),
        ("https://acme.example/partners", "clients"),
        ("https://acme.example/clients", "clients"),
        ("https://acme.example/certifications", "certifications"),
        ("https://acme.example/sustainability", "sustainability"),
        ("https://acme.example/about", "about"),
        ("https://acme.example/المستثمرين", "investors"),
        ("https://acme.example/ar/علاقات-المستثمرين", "investors"),
        ("https://acme.example/ar/التقارير", "resources"),
        ("https://acme.example/ar/الموارد", "resources"),
        ("https://acme.example/ar/التحميلات", "resources"),
        ("https://acme.example/ar/المركز-الإعلامي", "media"),
        ("https://acme.example/ar/فريق-العمل", "team"),
        ("https://acme.example/ar/مجلس-الإدارة", "team"),
        ("https://acme.example/ar/تاريخنا", "history"),
        ("https://acme.example/ar/فروعنا", "locations"),
        ("https://acme.example/ar/شركاؤنا", "clients"),
        ("https://acme.example/ar/عملاؤنا", "clients"),
        ("https://acme.example/ar/الشهادات", "certifications"),
        ("https://acme.example/ar/الاستدامة", "sustainability"),
    ],
)
def test_the_section_words_in_english_and_arabic_score_a_page(url, section):
    points, found = rules.score(url)
    assert points > 0 and found == section


def test_the_website_import_keywords_still_count_and_an_unrelated_page_scores_nothing():
    assert rules.score("https://acme.example/products")[1] == "products"
    assert rules.score("https://acme.example/contact")[1] == "contact"
    assert rules.score("https://acme.example/blog/office-party")[0] == 0


def test_a_more_important_section_scores_higher_and_nav_links_get_a_bonus():
    assert rules.score("https://acme.example/about")[0] > rules.score("https://acme.example/careers")[0]
    assert rules.score("https://acme.example/investors")[0] > rules.score("https://acme.example/media")[0]
    assert rules.score("https://acme.example/x", "Investor relations", in_nav=True)[1] == "investors"
    assert rules.score("https://acme.example/investors", in_nav=True)[0] > rules.score("https://acme.example/investors")[0]


def test_the_careers_index_is_read_and_a_job_ad_under_it_is_not():
    assert rules.skip_reason("https://acme.example/careers") is None
    assert rules.skip_reason("https://acme.example/careers/senior-engineer") == "job ad"
    assert rules.skip_reason("https://acme.example/ar/careers/senior-engineer") == "job ad"
    assert rules.skip_reason("https://acme.example/jobs/42") == "job ad"


@pytest.mark.parametrize(
    "url, reason",
    [
        ("https://acme.example/login", "page type"),
        ("https://acme.example/login.php", "page type"),
        ("https://acme.example/signup", "page type"),
        ("https://acme.example/sign-up", "page type"),
        ("https://acme.example/cart", "page type"),
        ("https://acme.example/shop/checkout", "page type"),
        ("https://acme.example/search", "page type"),
        ("https://acme.example/products?q=pos", "search"),
        ("https://acme.example/?s=pos", "search"),
        ("https://acme.example/forms/apply", "page type"),
        ("https://acme.example/images/logo.png", "file"),
        ("https://acme.example/files/brochure.zip", "file"),
        ("https://acme.example/data.json", "file"),
        ("https://acme.example/docs/report.pdf", "pdf"),
        ("https://acme.example/2023/05/launch", "archive"),
        ("https://acme.example/ar/تسجيل-الدخول", "page type"),
    ],
)
def test_pages_a_crawl_does_not_read(url, reason):
    assert rules.skip_reason(url) == reason


@pytest.mark.parametrize(
    "url",
    ["https://acme.example/", "https://acme.example/about", "https://acme.example/about.html", "https://acme.example/index.php",
     "https://acme.example/ar/about", "https://acme.example/news?id=5", "https://acme.example/v1.2/overview"],
)
def test_html_pages_are_read(url):
    assert rules.skip_reason(url) is None


def test_query_string_copies_are_one_page_but_the_language_parameter_is_not():
    key = rules.page_key("https://acme.example/about")
    assert key == rules.page_key("https://www.acme.example/about/")
    assert key == rules.page_key("http://acme.example/about?sort=asc&page=2")
    assert key == rules.page_key("https://acme.example/About#team")
    assert rules.page_key("https://acme.example/index.html") == rules.page_key("https://acme.example/")
    assert rules.page_key("https://acme.example/about?lang=ar") != key


def test_the_english_and_arabic_copies_of_a_page_share_a_twin_key():
    key = rules.twin_key("https://acme.example/team")
    assert rules.twin_key("https://acme.example/ar/team") == key
    assert rules.twin_key("https://acme.example/en/team/") == key
    assert rules.twin_key("https://ar.acme.example/team") == key
    assert rules.twin_key("https://acme.example/team?lang=ar") == key
    assert rules.twin_key("https://acme.example/team/board") != key


def test_scope_is_the_registered_domain_and_its_subdomains():
    home = "https://www.acme.example/"
    assert rules.in_scope("https://acme.example/about", home)
    assert rules.in_scope("https://news.acme.example/", home)
    assert rules.in_scope("https://deep.shop.acme.example/", home)
    assert not rules.in_scope("https://acme.example.evil.example/", home)
    assert not rules.in_scope("https://notacme.example/", home)
    assert not rules.in_scope("https://facebook.com/acme", home)
    assert rules.in_scope("https://shop.acme.com.eg/", "https://www.acme.com.eg/")
    assert not rules.in_scope("https://other.com.eg/", "https://www.acme.com.eg/")


@pytest.mark.parametrize(
    "url, text, expected",
    [
        ("https://acme.example/docs/a.pdf", "", True),
        ("https://acme.example/docs/A.PDF", "", True),
        ("https://acme.example/download.php?file=report.pdf", "", True),
        ("https://acme.example/docs/a.pdf#page=3", "", True),
        ("https://acme.example/get/42", "Annual report (PDF)", True),
        ("https://acme.example/get/42", "Download PDF", True),
        ("https://acme.example/pdfs-explained", "About", False),
        ("https://acme.example/about", "About us", False),
    ],
)
def test_links_that_look_like_a_pdf(url, text, expected):
    assert rules.is_pdf_link(url, text) is expected


def test_signs_that_a_page_builds_its_content_with_scripts():
    assert "empty app root" in rules.client_side_signs('<body><div id="root"></div><script src=a.js></script></body>')
    assert "empty app root" in rules.client_side_signs('<div id="__next"> </div>')
    assert "asks for JavaScript" in rules.client_side_signs("<noscript>You need to enable JavaScript to run this app.</noscript>")
    assert "lazy loading" in rules.client_side_signs('<button class="load-more">Show</button>')
    assert "lazy loading" in rules.client_side_signs("<a href='#'> عرض المزيد </a>")
    assert "lazy loading" in rules.client_side_signs('<div class="infinite-scroll"></div>')
    assert rules.client_side_signs('<body><div id="root"><p>Already here</p></div></body>') == []


def test_the_browser_is_wanted_for_thin_text_or_script_signs_only():
    page = '<div id="root"><p>x</p></div>'
    assert "less than 300" in rules.render_reason("short", page, 300)
    assert rules.render_reason("t" * 300, "<p>plain</p>", 300) == ""
    assert rules.render_reason("t" * 300, '<button class="load-more">More</button>', 300) == "lazy loading"


def test_a_page_with_a_password_field_is_a_login_page():
    assert rules.is_login_page('<form><input type="password" name="pw"></form>')
    assert rules.is_login_page("<input type=password>")
    assert not rules.is_login_page('<input type="text" name="q">')
