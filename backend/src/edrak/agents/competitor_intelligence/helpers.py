from datetime import datetime
from typing import Any


CURRENT_YEAR = datetime.now().year

MAX_REQUIREMENTS_PER_COMPETITOR = 12
MAX_PRODUCTS_PER_COMPETITOR = 15

MAX_QUERIES_PER_REQUIREMENT = 3
MAX_QUERIES_TOTAL = 100

RESEARCH_BATCH_SIZE = 4


def get_state_value(state, key, default=None):
    return state.get(key, default)


def get_competitor_names(state):
    competitors = state.get("competitors", [])

    names = []

    for competitor in competitors:
        if isinstance(competitor, dict):
            name = (
                competitor.get("name")
                or competitor.get("competitor")
            )
        else:
            name = str(competitor)

        if name and name.strip():
            names.append(name.strip())

    return names


def normalize_text(value):
    return " ".join(
        str(value or "").lower().strip().split()
    )


def unique_items(items):
    result = []
    seen = set()

    for item in items:
        normalized = normalize_text(item)

        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(item)

    return result


def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def safe_to_dict(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()

    if isinstance(value, dict):
        return value

    return {}