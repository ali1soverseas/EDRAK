"""Parse a number out of source text. Handles Arabic digits, currency symbols, k/M/B and %."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_ARABIC = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_CURRENCY = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
             "egp": "EGP", "ج.م": "EGP", "sar": "SAR", "aed": "AED"}
_MULT = {"k": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9, "t": 1e12,
         "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_NUM = re.compile(
    r"(?P<cur1>\$|€|£)?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<mult>thousand|million|billion|trillion|mm|mn|bn|[kmbt])?\b\s*(?P<pct>%|percent)?"
    r"(?:\s*(?P<cur2>usd|eur|gbp|egp|sar|aed))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedNumber:
    value: float
    unit: Optional[str]  # "%", an ISO currency code, or None
    text: str


def normalize_digits(text: str) -> str:
    return text.translate(_ARABIC)


# "$7–8 billion" and "70-73%": the scale or percent sign written once after a range applies to both ends.
_RANGE = re.compile(
    r"(?P<a>\d+(?:\.\d+)?)(?P<sep>\s*(?:–|—|-|to)\s*)(?P<b>[$€£]?\s*\d+(?:\.\d+)?)\s*(?P<s>thousand|million|billion|trillion|bn|mn|%)",
    re.IGNORECASE,
)


def _spread_range_unit(text: str) -> str:
    return _RANGE.sub(lambda m: f"{m['a']} {m['s']}{m['sep']}{m['b']} {m['s']}", text)


def parse_numbers(text: str) -> list[ParsedNumber]:
    out: list[ParsedNumber] = []
    for m in _NUM.finditer(_spread_range_unit(normalize_digits(text or ""))):
        raw = m.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        mult = (m.group("mult") or "").lower()
        # a bare trailing "m"/"b"/"t" inside a word is not a multiplier
        if mult in _MULT:
            value *= _MULT[mult]
        unit: Optional[str] = None
        if m.group("pct"):
            unit = "%"
        else:
            cur = (m.group("cur1") or m.group("cur2") or "").lower()
            unit = _CURRENCY.get(cur)
        out.append(ParsedNumber(value=value, unit=unit, text=m.group(0).strip()))
    return out


def parse_value(text: str) -> Optional[ParsedNumber]:
    """The first number in text, or None."""
    found = parse_numbers(text)
    return found[0] if found else None


def appears_in(value_text: str, source: str) -> bool:
    """True when the number written in value_text is present in the source (format-insensitive)."""
    wanted = parse_numbers(value_text)
    if not wanted:
        return False
    have = {round(p.value, 6) for p in parse_numbers(source)}
    return all(round(w.value, 6) in have for w in wanted[:1])


def span_in_source(span: str, source: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", normalize_digits(s or "")).strip().lower()
    return bool(span) and norm(span) in norm(source)
