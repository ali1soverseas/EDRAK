import pytest

from edrak.agents.customer_trends.utils.text import (
    content_hash,
    detect_language,
    evidence_id,
    normalize_text,
    search_key,
    truncate,
)

ARABIC = "الدعم الفني بطيء جدا ومكلف ولا يرد على الرسائل في الوقت المناسب"
ENGLISH = "The support team is slow to reply and the pricing keeps going up every year"


def test_normalize_collapses_whitespace_and_applies_nfkc() -> None:
    assert normalize_text("  hello \n\t world  ") == "hello world"
    assert normalize_text("ｆｕｌｌｗｉｄｔｈ ① ﬁ") == "fullwidth 1 fi"
    assert normalize_text("") == ""


def test_normalize_strips_arabic_diacritics_and_tatweel() -> None:
    assert normalize_text("مَدْرَسَة") == "مدرسة"
    assert normalize_text("جـــميل") == "جميل"
    assert normalize_text("الدعم  الفني") == "الدعم الفني"


def test_search_key_ignores_case_and_folds_arabic_spelling_variants() -> None:
    assert search_key("GitLab  DUO") == search_key("gitlab duo")
    assert search_key("أمازون") == search_key("امازون") == search_key("إمازون")
    assert search_key("مدرسة") == search_key("مدرسه")
    assert search_key("على") == search_key("علي")
    assert search_key("مَدْرَسَة") == search_key("مدرسه")
    assert search_key("أحمد") != search_key("محمد")


def test_normalize_keeps_arabic_letters_and_mixed_text() -> None:
    assert normalize_text("GitLab Duo رائع!") == "GitLab Duo رائع!"


def test_content_hash_is_stable_and_ignores_cosmetic_differences() -> None:
    base = content_hash("مَدْرَسَة   جميلة")
    assert base == content_hash("مدرسة جميلة")
    assert content_hash("جـــميل") == content_hash("جميل")
    assert content_hash("Hello World") == content_hash("hello   WORLD")
    assert content_hash("Hello") != content_hash("Hello!")
    assert len(base) == 40
    assert all(c in "0123456789abcdef" for c in base)


def test_content_hash_of_empty_and_emoji_text_is_defined() -> None:
    assert content_hash("") == content_hash("   ")
    assert content_hash("🔥🔥") != content_hash("🔥")


def test_evidence_id_is_deterministic_16_hex() -> None:
    first = evidence_id("reddit|abc")
    assert first == evidence_id("reddit|abc")
    assert first != evidence_id("reddit|abd")
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)
    assert len(evidence_id("")) == 16
    assert evidence_id("قيمة") == evidence_id("قيمة")


def test_truncate() -> None:
    assert truncate("short", 10) == "short"
    assert truncate("abcdefghij", 10) == "abcdefghij"
    cut = truncate("abcdefghijk", 10)
    assert len(cut) == 10
    assert cut.endswith("\N{HORIZONTAL ELLIPSIS}")
    assert truncate("abcdef", 3, suffix="") == "abc"
    assert truncate("abcdef", 1) == "a"
    assert truncate("", 5) == ""
    arabic = truncate("ا" * 300, 240)
    assert len(arabic) == 240
    with pytest.raises(ValueError, match="non-negative"):
        truncate("x", -1)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (ARABIC, "ar"),
        (ENGLISH, "en"),
        ("جيد", "ar"),
        ("تطبيق ممتاز", "ar"),
        ("GitLab Duo شغال كويس بس بطيء", "ar"),
        ("ممتاز https://example.test/very/long/link #devops @someone", "ar"),
    ],
)
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_detect_language_mixed_text_follows_the_dominant_script() -> None:
    assert detect_language(f"{ARABIC} GitLab Duo") == "ar"
    assert detect_language(f"{ENGLISH} الدعم") == "en"


@pytest.mark.parametrize(
    "text", ["", "   ", "🔥🔥🔥", "12345", "!!! ??? ...", "https://example.test"]
)
def test_detect_language_returns_none_without_letters(text: str) -> None:
    assert detect_language(text) is None


@pytest.mark.parametrize("text", ["ok", "good", "xx", "bad", "Great product", "Support is slow"])
def test_detect_language_is_none_for_tiny_latin_text(text: str) -> None:
    assert detect_language(text) is None


def test_detect_language_is_deterministic() -> None:
    assert {detect_language(ENGLISH) for _ in range(20)} == {"en"}
