import pytest

from edrak.company_research.categories import CATEGORY_KEYS, OTHER
from edrak.company_research.keys import normalize_key, normalize_phrase
from edrak.company_research.models import FactKeySpec, Synonyms
from edrak.company_research.seed_keys import SEED_KEYS


@pytest.mark.parametrize(
    "category, proposed, expected",
    [
        ("leadership", "ceo", "ceo"),
        ("leadership", "CEO", "ceo"),
        ("leadership", "Chief Executive Officer", "ceo"),
        ("leadership", "chief_executive_officer", "ceo"),
        ("leadership", "Founder", "founder"),
        ("leadership", "Co-Founder", "founder"),
        ("leadership", "Board Members", "board_member"),
        ("leadership", "Chairman of the Board", "chairman"),
        ("history", "founded", "founded_year"),
        ("history", "Year Founded:", "founded_year"),
        ("locations", "Headquarters", "headquarters"),
        ("locations", "head office", "headquarters"),
        ("locations", "Branches", "branch"),
        ("locations", "factories", "factory"),
        ("team", "Number of Employees", "employee_count"),
        ("team", "headcount", "employee_count"),
        ("certifications", "ISO 9001", "iso_certification"),
    ],
)
def test_english_labels_and_synonyms_find_the_canonical_key(category, proposed, expected):
    assert normalize_key(category, proposed) == expected


@pytest.mark.parametrize(
    "category, proposed, expected",
    [
        ("leadership", "الرئيس التنفيذي", "ceo"),
        ("leadership", "الرئيس التنفيذى", "ceo"),  # final yeh written as alef maksura
        ("leadership", "المؤسس", "founder"),
        ("leadership", "رئيس مجلس الإدارة", "chairman"),
        ("leadership", "رئيس مجلس الاداره", "chairman"),  # no hamza, teh marbuta as heh
        ("leadership", "أعضاء مجلس الإدارة", "board_member"),
        ("history", "سنة التأسيس", "founded_year"),
        ("history", "سنة التاسيس", "founded_year"),
        ("locations", "المقر الرئيسي", "headquarters"),
        ("locations", "الفروع", "branch"),
        ("locations", "مصنع", "factory"),
        ("team", "عدد الموظفين", "employee_count"),
        ("certifications", "آيزو", "iso_certification"),
        ("certifications", "ايزو", "iso_certification"),
    ],
)
def test_arabic_labels_and_synonyms_find_the_canonical_key(category, proposed, expected):
    assert normalize_key(category, proposed) == expected


def test_arabic_diacritics_and_tatweel_are_ignored():
    assert normalize_key("leadership", "الرَّئيس التنفيذي") == "ceo"
    assert normalize_key("leadership", "المـــؤسس") == "founder"


def test_punctuation_and_spacing_do_not_matter():
    assert normalize_key("leadership", "  C.E.O.  ") is None  # dots split letters: not the same phrase
    assert normalize_key("leadership", "Chief   Executive,  Officer!") == "ceo"
    assert normalize_key("history", "year-founded") == "founded_year"
    assert normalize_key("history", "year_founded") == "founded_year"
    assert normalize_key("history", "الاسم السابق؟") == "former_name"


def test_a_key_is_only_found_in_its_own_category():
    assert normalize_key("history", "ceo") is None
    assert normalize_key("locations", "founder") is None


def test_unknown_blank_and_other_return_none():
    assert normalize_key("leadership", "favourite colour") is None
    assert normalize_key("leadership", "") is None
    assert normalize_key("leadership", "   ") is None
    assert normalize_key("leadership", "!!!") is None
    assert normalize_key(OTHER, "ceo") is None
    assert normalize_key("not_a_category", "ceo") is None


def test_a_longer_phrase_is_not_matched_by_a_word_inside_it():
    assert normalize_key("leadership", "ceo compensation package") is None


def test_promoted_keys_can_be_matched_through_the_keys_argument():
    promoted = FactKeySpec(
        category="leadership", key="head_of_sales", label_en="Head of sales", label_ar="رئيس المبيعات",
        synonyms=Synonyms(en=["sales director"], ar=["مدير المبيعات"]),
    )
    assert normalize_key("leadership", "Sales Director", [promoted]) == "head_of_sales"
    assert normalize_key("leadership", "مدير المبيعات", [promoted]) == "head_of_sales"
    assert normalize_key("leadership", "ceo", [promoted]) is None  # only the keys given are matched
    assert normalize_key("leadership", "Sales Director") is None  # and the seeds do not have it


def test_plural_folding_keeps_words_that_end_in_s_by_nature():
    assert normalize_phrase("address") == "address"
    assert normalize_phrase("status") == "status"
    assert normalize_phrase("Boards") == "board"
    assert normalize_phrase("companies") == "company"


def test_every_seed_key_is_found_by_its_key_label_and_synonyms():
    for spec in SEED_KEYS:
        phrases = [spec.key, spec.label_en, spec.label_ar, *spec.synonyms.en, *spec.synonyms.ar]
        for phrase in phrases:
            assert normalize_key(spec.category, phrase) == spec.key, (spec.category, spec.key, phrase)


def test_seed_keys_are_well_formed_and_unambiguous():
    assert not any(k.category == OTHER for k in SEED_KEYS)
    seen_keys = set()
    claimed: dict[tuple[str, str], str] = {}
    for spec in SEED_KEYS:
        assert spec.category in CATEGORY_KEYS
        assert (spec.category, spec.key) not in seen_keys
        seen_keys.add((spec.category, spec.key))
        assert spec.synonyms.en and spec.synonyms.ar, (spec.category, spec.key)
        for phrase in [spec.key, spec.label_en, spec.label_ar, *spec.synonyms.en, *spec.synonyms.ar]:
            folded = normalize_phrase(phrase)
            owner = claimed.setdefault((spec.category, folded), spec.key)
            assert owner == spec.key, f"{phrase!r} names both {owner} and {spec.key} in {spec.category}"


def test_every_category_but_other_has_seed_keys_with_an_arabic_synonym():
    for category in CATEGORY_KEYS:
        keys = [k for k in SEED_KEYS if k.category == category]
        if category == OTHER:
            assert not keys
            continue
        assert keys, category
        assert any("؀" <= ch <= "ۿ" for k in keys for s in k.synonyms.ar for ch in s)
