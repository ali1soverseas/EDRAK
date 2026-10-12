"""Format a stored value for display. Pure code: the only way a number reaches the screen."""

from __future__ import annotations

from typing import Optional

_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£"}


def _short(v: float) -> str:
    a = abs(v)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e4, "K")):
        if a >= limit:
            return f"{v / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")


def format_value(value: Optional[float], unit: Optional[str]) -> str:
    if value is None:
        return "—"
    if unit == "%":
        return f"{value:g}%" if abs(value) < 1000 else _short(value) + "%"
    if unit in _SYMBOL:
        return _SYMBOL[unit] + _short(value)
    if unit:
        return f"{_short(value)} {unit}"
    return _short(value)
