from edrak.company_research.categories import CATEGORIES, CATEGORY_KEYS, OTHER, get_category, is_category

EXPECTED = [
    "leadership", "ownership_and_funding", "financials", "customers", "partners", "locations", "team",
    "pricing", "technology", "certifications", "regulatory", "news_and_events", "awards", "history", "other",
]


def test_the_category_list_is_the_agreed_one_in_order():
    assert list(CATEGORY_KEYS) == EXPECTED
    assert OTHER == "other"


def test_every_category_has_an_english_and_an_arabic_label():
    for category in CATEGORIES:
        assert category.label_en.strip()
        assert any("؀" <= ch <= "ۿ" for ch in category.label_ar), category.key
        assert category.key == category.key.lower() and " " not in category.key


def test_lookup_by_key():
    assert get_category("leadership").label_ar == "القيادة"
    assert get_category("nonsense") is None
    assert is_category("history") and not is_category("History")
