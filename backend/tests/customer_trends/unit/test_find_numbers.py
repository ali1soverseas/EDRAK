import pytest

from edrak.agents.customer_trends.schemas.findings import find_numbers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("no numbers here", []),
        ("12 complaints", [12.0]),
        ("3.5 stars and 0.25 share", [3.5, 0.25]),
        ("1,200 users and 1,234,567.89 views", [1200.0, 1234567.89]),
        ("45% of users", [45.0]),
        ("growth of 12.5%", [12.5]),
        ("from 10 to 20 mentions", [10.0, 20.0]),
        ("down -5 points and -7.5 percent", [-5.0, -7.5]),
        ("2020-2021 season", [2020.0, 2021.0]),
        ("Q3 and B2B and GPT4 and v2 grew 12", [12.0]),
        ("10x faster", [10.0]),
    ],
)
def test_western_digits(text: str, expected: list[float]) -> None:
    assert find_numbers(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("٤٥٪ من المستخدمين", [45.0]),
        ("١٢ شكوى", [12.0]),
        ("٣٫٥ نجوم", [3.5]),
        ("١٬٢٠٠ مستخدم", [1200.0]),
        ("۱۲ و ۳٫۵", [12.0, 3.5]),
        ("وجدنا 12 شكوى و٣ ردود", [12.0, 3.0]),
        ("−٥ نقاط", [-5.0]),
    ],
)
def test_arabic_indic_digits(text: str, expected: list[float]) -> None:
    assert find_numbers(text) == expected


def test_mixed_script_text_keeps_order() -> None:
    assert find_numbers("Support 12 ثم ٣٠٪ then 4.5") == [12.0, 30.0, 4.5]
