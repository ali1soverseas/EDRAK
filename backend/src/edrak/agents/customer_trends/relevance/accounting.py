"""What the filter did, counted from the marks it left in the store."""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from edrak.agents.customer_trends.relevance.models import UNKNOWN_PLATFORM

CHARS_PER_TOKEN = (
    4  # a rough rule for the estimate; the real count depends on the model and language
)
ESTIMATE_NOTE = f"tokens are an estimate: characters divided by {CHARS_PER_TOKEN}"


def estimate_tokens(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN)  # rounded up


def _blank() -> dict[str, Any]:
    return {
        "comments_in": 0,
        "dropped": {},
        "kept": 0,
        "kept_by_reason": {},
        "sent_to_llm": 0,
        "tokens_before": 0,
        "tokens_after": 0,
    }


def summarize(rows: Iterable[dict[str, Any]], item_types: Sequence[str]) -> dict[str, Any]:
    """Run totals and per-platform counts from `EvidenceStore.relevance_rows`.

    `comments_in` is every item the filter judged, `dropped` counts the irrelevant ones by reason,
    `kept` the relevant ones, `sent_to_llm` those Stage C looked at (answered or not). The token
    figures are character counts divided by four, before and after the filter.
    """
    total = _blank()
    by_platform: dict[str, dict[str, Any]] = defaultdict(_blank)
    chars_before: dict[str, int] = defaultdict(int)
    chars_after: dict[str, int] = defaultdict(int)
    for row in rows:
        platform = str(row["platform"] or UNKNOWN_PLATFORM)
        n, chars = int(row["n"]), int(row["chars"] or 0)
        for scope, key in ((total, "*"), (by_platform[platform], platform)):
            scope["comments_in"] += n
            chars_before[key] += chars
            if row["stage"] == "c":
                scope["sent_to_llm"] += n
            if row["relevance"] == "irrelevant":
                scope["dropped"][row["reason"]] = scope["dropped"].get(row["reason"], 0) + n
            else:
                scope["kept"] += n
                chars_after[key] += chars
                scope["kept_by_reason"][row["reason"]] = (
                    scope["kept_by_reason"].get(row["reason"], 0) + n
                )
    total["tokens_before"], total["tokens_after"] = (
        estimate_tokens(chars_before["*"]),
        estimate_tokens(chars_after["*"]),
    )
    for platform, scope in by_platform.items():
        scope["tokens_before"] = estimate_tokens(chars_before[platform])
        scope["tokens_after"] = estimate_tokens(chars_after[platform])
    return {
        "enabled": True,
        "item_types": list(item_types),
        "token_estimate": ESTIMATE_NOTE,
        **total,
        "by_platform": dict(sorted(by_platform.items())),
    }


def disabled(item_types: Sequence[str]) -> dict[str, Any]:
    return {"enabled": False, "item_types": list(item_types)}
