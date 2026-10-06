import pytest

from edrak.agents.customer_trends.utils.labels import jaccard, label_tokens, normalize_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Slow Customer Support", "slow customer support"),
        ("slow  customer-support!", "slow customer support"),
        ("Slow customer supports", "slow customer support"),
        ("Missing Excel exports", "missing excel export"),
        ("pricing concerns", "pricing concern"),
        ("Billing queries", "billing query"),
        ("Access", "access"),
        ("Analysis", "analysis"),
        ("Status", "status"),
        ("Boxes", "box"),
        ("Lack of the integrations", "lack integration"),
        ("The", "the"),
    ],
)
def test_labels_are_case_punctuation_and_plural_insensitive(label: str, expected: str) -> None:
    assert normalize_label(label) == expected


def test_arabic_labels_are_matched_without_diacritics_or_spelling_variants() -> None:
    assert normalize_label("الدعم الفني") == normalize_label("الدَّعْم  الفني")
    assert normalize_label("أسعار مرتفعة") == normalize_label("اسعار مرتفعه")


def test_tokens_keep_their_order_and_the_key_is_the_joined_tokens() -> None:
    assert label_tokens("Support of the Team") == ["support", "team"]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ({"a", "b"}, {"a", "b"}, 1.0),
        ({"a", "b"}, {"b", "c"}, 1 / 3),
        ({"slow", "customer", "support"}, {"slow", "support"}, 2 / 3),
        ({"a"}, {"b"}, 0.0),
        (set(), set(), 0.0),
    ],
)
def test_jaccard(left: set[str], right: set[str], expected: float) -> None:
    assert jaccard(left, right) == pytest.approx(expected)
