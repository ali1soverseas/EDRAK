"""Histories that turn each criteria pair into a decision-agent emphasis.

The agent does not see the tags. It sees one sentence from ``emphasis_text``.
Two histories per pair change that sentence. A third keeps the narrower
sentence even when one criterion outweighs the other.
"""

from __future__ import annotations

import json
from typing import Any

from .personalization import (
    CRITERIA,
    RELATIONS,
    emphasis_text,
    infer_preference,
    note_text,
)

KINDS = ("together", "outweighed", "split")
_ORDER = list(CRITERIA)


def pairs() -> list[tuple[str, str, dict[str, str]]]:
    """Each defined pair, members in criteria order."""
    found = []
    for members, spec in RELATIONS.items():
        left, right = sorted(members, key=_ORDER.index)
        found.append((left, right, spec))
    return found


def history_for(left: str, right: str, kind: str) -> list[dict[str, Any]]:
    """Saved-decision rows that should produce this kind of emphasis.

    ``together`` puts both criteria on the same two decisions.
    ``outweighed`` does the same, and the typed reason names the first criterion,
    so its score is higher. The pair still narrows the focus.
    ``split`` puts each criterion on its own decisions.
    """
    if kind == "together":
        return [_row([left, right], title=f"{left} and {right}"), _row([left, right], title=f"{left} and {right} again")]
    if kind == "outweighed":
        return [
            _row([left, right], [left], title=f"{left} stated with {right}"),
            _row([left, right], [left], title=f"{left} stated with {right} again"),
        ]
    if kind == "split":
        return [
            _row([left], title=f"{left} only"),
            _row([left], title=f"{left} only again"),
            _row([right], title=f"{right} only"),
            _row([right], title=f"{right} only again"),
        ]
    raise ValueError(f"unknown case kind: {kind}")


def build_cases() -> list[dict[str, Any]]:
    """One record per pair and kind, including the sentence the agent would receive."""
    cases = []
    for left, right, spec in pairs():
        for kind in KINDS:
            preference = infer_preference(history_for(left, right, kind))
            emphasis = emphasis_text(preference)
            cases.append({
                "id": f"{spec['id']}__{kind}",
                "relation": spec["id"],
                "label": spec["label"],
                "pair": [left, right],
                "kind": kind,
                "preference": preference,
                "emphasis": emphasis,
                "note": note_text(preference),
                "checks": _checks(left, right, spec, kind),
            })
    return cases


def _row(option: list[str], reason: list[str] | None = None, title: str = "") -> dict[str, Any]:
    tags = {"version": 2, "option": list(option), "reason": list(reason or [])}
    return {
        "id": title,
        "title": title,
        "option_label": "A",
        "preference_tags": json.dumps(tags),
    }


def _checks(left: str, right: str, spec: dict[str, str], kind: str) -> str:
    shared = "Options stay unranked. The text does not mention past decisions."
    if kind == "split":
        return (
            f"Each option makes the trade-off between {CRITERIA[left]['focus']} "
            f"and {CRITERIA[right]['focus']} explicit. {shared}"
        )
    return (
        f"The rationale, actions, risks, success criteria, and each option's thesis, "
        f"trade-offs and risks examine {spec['focus']}. {shared}"
    )
