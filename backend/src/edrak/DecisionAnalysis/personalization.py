"""Turn saved decision tags into one analysis emphasis.

The emphasis tells the existing analysis which criterion to examine more deeply.
It does not rank options, remove options, or choose one.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable

from edrak.db.util import fetch_all, loads

logger = logging.getLogger(__name__)

TAGS_VERSION = 2
HISTORY_POOL = 50
CRITERIA: dict[str, dict[str, Any]] = {
    "speed": {
        "label": "implementation time",
        "focus": "time to market, implementation time and integration effort",
        "keywords": ("fast", "quick", "speed", "time to market", "sooner", "deadline", "urgent", "immediately"),
    },
    "cost": {
        "label": "total cost",
        "focus": "total cost of ownership, operating cost and return on investment",
        "keywords": ("cost", "cheap", "budget", "price", "saving", "roi", "affordable", "lower spend"),
    },
    "control": {
        "label": "in-house control",
        "focus": "in-house feasibility, staffing, control and long-term maintenance",
        "keywords": ("in-house", "internal", "own team", "control", "ownership", "build ourselves"),
    },
    "quality": {
        "label": "quality",
        "focus": "product quality, customer experience and fit with requirements",
        "keywords": ("quality", "customer experience", "premium", "polish", "satisfaction"),
    },
    "risk": {
        "label": "reliability and risk",
        "focus": "reliability evidence, failure risks and mitigation",
        "keywords": ("risk", "safe", "reliable", "proven", "stable", "compliance", "secure", "wait"),
    },
    "scale": {
        "label": "scale",
        "focus": "reach, capacity, volume and market share",
        "keywords": ("scale", "capacity", "volume", "market share"),
    },
    "differentiation": {
        "label": "differentiation",
        "focus": "positioning, switching and standing apart",
        "keywords": ("differentiation", "positioning", "switching", "stand apart"),
    },
    "reversibility": {
        "label": "reversibility",
        "focus": "whether the path can be piloted, undone or exited",
        "keywords": ("pilot", "lock-in", "reversible", "undo", "exit"),
    },
    "compliance": {
        "label": "compliance",
        "focus": "regulation, privacy, data residency and audit",
        "keywords": ("regulation", "privacy", "data residency", "audit", "compliant"),
    },
    "revenue": {
        "label": "revenue",
        "focus": "pricing power, margin, monetization and upside",
        "keywords": ("pricing power", "margin", "monetization", "upside"),
    },
    "customer": {
        "label": "customers",
        "focus": "adoption, retention, loyalty and demand",
        "keywords": ("adoption", "retention", "loyalty", "demand"),
    },
    "partnership": {
        "label": "partnership",
        "focus": "alliances, channels, ecosystem and joint go-to-market",
        "keywords": ("partnership", "alliance", "ecosystem", "joint go-to-market"),
    },
    "talent": {
        "label": "talent",
        "focus": "hiring, skills and expertise",
        "keywords": ("hiring", "skills", "expertise"),
    },
}
RELATIONS: dict[frozenset[str], dict[str, str]] = {
    frozenset(("speed", "cost")): {"id": "time_to_value", "label": "time to value", "focus": "how quickly the spend is recovered, and the cost of waiting"},
    frozenset(("speed", "quality")): {"id": "quality_at_speed", "label": "quality at speed", "focus": "whether the faster path still meets the quality bar"},
    frozenset(("speed", "risk")): {"id": "timing_risk", "label": "timing risk", "focus": "the risk of moving early against the risk of waiting"},
    frozenset(("speed", "control")): {"id": "in_house_timeline", "label": "in-house timeline", "focus": "whether building internally can still hit the date"},
    frozenset(("cost", "risk")): {"id": "cost_of_failure", "label": "cost of failure", "focus": "downside exposure, not average cost"},
    frozenset(("cost", "control")): {"id": "cost_of_ownership", "label": "cost of ownership", "focus": "the cost of staffing and keeping it in-house"},
    frozenset(("cost", "quality")): {"id": "spend_and_outcome", "label": "where spend changes the outcome", "focus": "where spend changes the customer outcome"},
    frozenset(("cost", "scale")): {"id": "unit_economics", "label": "unit economics as volume grows", "focus": "unit economics as volume grows"},
    frozenset(("control", "risk")): {"id": "who_carries_failure", "label": "who carries the failure", "focus": "who carries the failure"},
    frozenset(("control", "quality")): {"id": "in_house_quality", "label": "in-house quality", "focus": "whether the in-house team can hold the quality bar"},
    frozenset(("quality", "risk")): {"id": "reliable_outcome", "label": "how reliable the customer outcome is", "focus": "how reliable the customer outcome is"},
    frozenset(("scale", "risk")): {"id": "capacity_risk", "label": "capacity and concentration risk", "focus": "capacity and concentration risk"},
    frozenset(("differentiation", "quality")): {"id": "switch_for", "label": "what a customer would switch for", "focus": "what a customer would switch for"},
    frozenset(("reversibility", "risk")): {"id": "undo_downside", "label": "how much of the downside can be undone", "focus": "how much of the downside can be undone"},
    frozenset(("reversibility", "cost")): {"id": "reversible_cost", "label": "the cost of a reversible path versus a commitment", "focus": "the cost of a reversible path versus a commitment"},
    frozenset(("compliance", "risk")): {"id": "regulatory_downside", "label": "regulatory downside", "focus": "regulatory downside"},
    frozenset(("compliance", "control")): {"id": "compliant_owner", "label": "who must own the compliant path", "focus": "who must own the compliant path"},
    frozenset(("revenue", "cost")): {"id": "margin", "label": "margin", "focus": "what is kept after the cost of delivering it"},
    frozenset(("revenue", "speed")): {"id": "revenue_speed", "label": "how soon the revenue arrives", "focus": "how soon the revenue arrives"},
    frozenset(("revenue", "scale")): {"id": "revenue_scale", "label": "how revenue changes as volume grows", "focus": "how revenue changes as volume grows"},
    frozenset(("revenue", "differentiation")): {"id": "price_hold", "label": "whether a distinct offer can hold its price", "focus": "whether a distinct offer can hold its price"},
    frozenset(("customer", "quality")): {"id": "keep_customers", "label": "whether the experience keeps customers", "focus": "whether the experience keeps customers"},
    frozenset(("customer", "risk")): {"id": "lose_customers", "label": "the risk of losing customers", "focus": "the risk of losing customers"},
    frozenset(("customer", "speed")): {"id": "customers_wait", "label": "whether customers will wait", "focus": "whether customers will wait"},
    frozenset(("customer", "differentiation")): {"id": "switch_and_stay", "label": "what would make a customer switch and stay", "focus": "what would make a customer switch and stay"},
    frozenset(("partnership", "control")): {"id": "partner_control", "label": "what ownership is given up to a partner", "focus": "what ownership is given up to a partner"},
    frozenset(("partnership", "speed")): {"id": "partner_speed", "label": "whether a partner reaches the market sooner", "focus": "whether a partner reaches the market sooner"},
    frozenset(("partnership", "cost")): {"id": "partner_cost", "label": "the cost of the partner path", "focus": "the cost of the partner path, including what is shared"},
    frozenset(("partnership", "compliance")): {"id": "partner_compliance", "label": "whether the partner can carry the regulatory burden", "focus": "whether the partner can carry the regulatory burden"},
    frozenset(("talent", "control")): {"id": "talent_control", "label": "whether the people exist to own it", "focus": "whether the people exist to own it"},
    frozenset(("talent", "speed")): {"id": "talent_speed", "label": "whether hiring can still hit the date", "focus": "whether hiring can still hit the date"},
    frozenset(("talent", "quality")): {"id": "talent_quality", "label": "whether the team can hold the quality bar", "focus": "whether the team can hold the quality bar"},
    frozenset(("talent", "scale")): {"id": "talent_scale", "label": "whether the team can support the volume", "focus": "whether the team can support the volume"},
}
RELATION_BY_ID = {spec["id"]: spec for spec in RELATIONS.values()}
DOMINANCE = 1.5
REASON_WEIGHT = 2.0
OPTION_WEIGHT = 1.0
NONE: dict[str, Any] = {"status": "none"}
_ARABIC = re.compile(r"[\u0600-\u06FF]")
Embedder = Callable[[list[str]], list[list[float]]]


def _hits(text: str) -> dict[str, int]:
    lowered = (text or "").lower()
    return {criterion: sum(lowered.count(word) for word in spec["keywords"]) for criterion, spec in CRITERIA.items()}


def _option_text(option: dict[str, Any]) -> str:
    opportunities = []
    for item in option.get("opportunities") or []:
        opportunities.append(item.get("text") if isinstance(item, dict) else str(item))
    return " ".join([
        option.get("title") or "",
        option.get("thesis") or "",
        *(option.get("what_we_would_do") or []),
        *opportunities,
    ])


def _ordered(names: set[str] | list[str]) -> list[str]:
    chosen = set(names)
    return [criterion for criterion in CRITERIA if criterion in chosen]


def tag_decision(
    options: list[dict[str, Any]],
    chosen_key: str | None,
    user_reason: str | None,
    translate: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Every aspect the chosen option stresses more than the alternatives, and every aspect the reason names.

    Trade-offs and risks are left out: they describe what the option gives up, not what the user preferred.
    A criterion mentioned equally by every option is not a preference. The option text stays as written.
    An Arabic reason is translated before the English keywords are counted. The stored reason is unchanged.
    """
    chosen = next((option for option in options if isinstance(option, dict) and option.get("key") == chosen_key), None)
    option_tags: list[str] = []
    if chosen:
        mine = _hits(_option_text(chosen))
        others = [_hits(_option_text(option)) for option in options if isinstance(option, dict) and option is not chosen]
        for criterion, count in mine.items():
            baseline = sum(hits[criterion] for hits in others) / len(others) if others else 0
            if count > 0 and count > baseline:
                option_tags.append(criterion)
    reason_text = user_reason or ""
    if _ARABIC.search(reason_text):
        if translate is None:
            reason_text = ""
        else:
            try:
                reason_text = translate(reason_text) or ""
            except Exception:
                logger.exception("Arabic rationale could not be translated")
                reason_text = ""
    reason_tags = [criterion for criterion, count in _hits(reason_text).items() if count > 0]
    return {"version": TAGS_VERSION, "option": option_tags, "reason": reason_tags}


async def load_history(db, workspace_id: str, use_case: str, limit: int = HISTORY_POOL) -> list[dict[str, Any]]:
    """Tagged, active decisions of the same kind. Undone and replaced decisions are superseded."""
    return await fetch_all(db, """
        SELECT d.id, d.title, d.option_label, d.preference_tags, d.recorded_at,
               json_extract(b.document, '$.question') AS question,
               json_extract(b.document, '$.request.business_context.markets') AS markets,
               json_extract(b.document, '$.request.business_context.targets') AS targets,
               json_extract(b.document, '$.request.business_context.offering') AS offering,
               json_extract(b.document, '$.request.business_context.competitors') AS competitors
        FROM decisions d JOIN briefs b ON b.id = d.brief_id
        WHERE d.workspace_id = ? AND b.use_case = ? AND d.status = 'decided'
          AND d.preference_tags IS NOT NULL
        ORDER BY d.recorded_at DESC LIMIT ?""", (workspace_id, use_case, limit))


def _names(value: Any) -> list[str]:
    parsed = loads(value, value) if isinstance(value, str) else value
    if isinstance(parsed, str):
        return [parsed] if parsed else []
    names: list[str] = []
    for item in parsed or []:
        if isinstance(item, dict):
            names.append(str(item.get("name") or ""))
        elif item:
            names.append(str(item))
    return [name for name in names if name]


def _request_data(request: Any) -> dict[str, Any]:
    if hasattr(request, "model_dump"):
        return request.model_dump(mode="json")
    return request if isinstance(request, dict) else {}


def request_text(request: Any) -> str:
    """The current question, plus the market, competitor and offering names."""
    data = _request_data(request)
    context = data.get("business_context") or {}
    competitors = _names(context.get("competitors")) + _names(context.get("targets"))
    parts = [data.get("goal") or "", context.get("offering") or "", " ".join(_names(context.get("markets"))), " ".join(competitors)]
    return " ".join(part for part in parts if part)


def past_text(row: dict[str, Any]) -> str:
    """The past brief question and decision title, plus the market, competitor and offering names.

    The stored rationale is left out. It is often generated plan text.
    """
    competitors = _names(row.get("competitors")) + _names(row.get("targets"))
    parts = [row.get("question") or "", row.get("title") or "", row.get("offering") or "", " ".join(_names(row.get("markets"))), " ".join(competitors)]
    return " ".join(part for part in parts if part)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = norm_left = norm_right = 0.0
    for a, b in zip(left, right):
        dot += a * b
        norm_left += a * a
        norm_right += b * b
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (math.sqrt(norm_left) * math.sqrt(norm_right))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """One batch from the local embedding model already used for document search."""
    from edrak.rag.embeddings import get_embedding_function

    vectors = get_embedding_function()(texts)
    return [[float(value) for value in vector] for vector in vectors]


def select_relevant(
    rows: list[dict[str, Any]],
    request: Any,
    *,
    k: int,
    threshold: float,
    aspect_bar: int,
    embed: Embedder,
) -> list[dict[str, Any]]:
    """Past decisions at or above the similarity threshold, highest first, at most k.

    Fewer passes than the aspect bar means personalization is not used.
    A failure of the embedder means the same: no rows, and the run continues.
    """
    if not rows or k <= 0:
        return []
    try:
        vectors = embed([request_text(request), *[past_text(row) for row in rows]])
    except Exception:
        logger.exception("Decision history could not be embedded")
        return []
    if len(vectors) != len(rows) + 1:
        logger.error("Embedding returned %s vectors for %s texts", len(vectors), len(rows) + 1)
        return []
    current = vectors[0]
    passed: list[tuple[float, str, dict[str, Any]]] = []
    for row, vector in zip(rows, vectors[1:]):
        score = _cosine(current, vector)
        if score >= threshold:
            passed.append((score, str(row.get("recorded_at") or ""), row))
    if len(passed) < aspect_bar:
        return []
    passed.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in passed[:k]]


def _parsed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = []
    for row in rows:
        tags = loads(row.get("preference_tags"), {}) or {}
        if not isinstance(tags, dict):
            continue
        scored = {criterion: OPTION_WEIGHT for criterion in tags.get("option") or [] if criterion in CRITERIA}
        for criterion in tags.get("reason") or []:
            if criterion in CRITERIA:
                scored[criterion] = REASON_WEIGHT
        parsed.append({
            "id": row.get("id"),
            "title": row.get("title"),
            "option": row.get("option_label"),
            "aspects": set(scored),
            "scores": scored,
        })
    return parsed


def _support(parsed: list[dict[str, Any]], criterion: str) -> list[dict[str, Any]]:
    return [{"id": row["id"], "title": row["title"], "option": row["option"]} for row in parsed if criterion in row["aspects"]]


def infer_preference(rows: list[dict[str, Any]], aspect_bar: int = 2) -> dict[str, Any]:
    """A relation when the same decisions carry both aspects, one aspect when history agrees, or none."""
    parsed = _parsed(rows)
    weight: dict[str, float] = {}
    for row in parsed:
        for criterion, score in row["scores"].items():
            weight[criterion] = weight.get(criterion, 0.0) + score
    cleared = [
        criterion for criterion, score in weight.items()
        if score >= aspect_bar or sum(1 for row in parsed if criterion in row["aspects"]) >= aspect_bar
    ]
    if not cleared:
        return dict(NONE)
    ranked = sorted(cleared, key=lambda criterion: weight[criterion], reverse=True)

    def together(left: str, right: str) -> list[dict[str, Any]]:
        return [row for row in parsed if left in row["aspects"] and right in row["aspects"]]

    best: tuple[float, str, str, dict[str, str], list[dict[str, Any]]] | None = None
    for index, left in enumerate(ranked):
        for right in ranked[index + 1:]:
            spec = RELATIONS.get(frozenset((left, right)))
            shared = together(left, right)
            if spec is None or len(shared) < aspect_bar:
                continue
            score = weight[left] + weight[right]
            if best is None or score > best[0]:
                best = (score, left, right, spec, shared)
    if best is not None:
        shared = best[4]
        present = {criterion for row in shared for criterion in row["aspects"] if criterion in cleared}
        return {
            "status": "applied",
            "relation": best[3]["id"],
            "aspects": _ordered(present),
            "decisions": [{"id": row["id"], "title": row["title"], "option": row["option"]} for row in shared],
        }

    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    if second and (len(together(top, second)) >= aspect_bar or weight[top] < DOMINANCE * weight[second]):
        seen = {item["id"] for item in _support(parsed, top)}
        return {
            "status": "uncertain",
            "criteria": [top, second],
            "decisions": _support(parsed, top) + [item for item in _support(parsed, second) if item["id"] not in seen],
        }
    return {"status": "applied", "criterion": top, "decisions": _support(parsed, top)}


def emphasis_text(preference: dict[str, Any] | None) -> str | None:
    """The one sentence the analysis prompts receive. None leaves the prompts unchanged."""
    status = (preference or {}).get("status")
    relation = RELATION_BY_ID.get((preference or {}).get("relation") or "")
    if status == "applied" and relation:
        return f"Examine {relation['focus']} in more depth."
    if status == "applied" and (preference or {}).get("criterion") in CRITERIA:
        return f"Examine {CRITERIA[preference['criterion']]['focus']} in more depth."
    criteria = (preference or {}).get("criteria") or []
    if status == "uncertain" and len(criteria) == 2 and all(criterion in CRITERIA for criterion in criteria):
        first, second = (CRITERIA[criterion]["focus"] for criterion in criteria)
        return f"Make the trade-off between {first} and {second} explicit."
    return None


def note_text(preference: dict[str, Any] | None) -> str | None:
    """The explanation shown with the options. Empty when history was not used."""
    status = (preference or {}).get("status")
    titles = ", ".join(item.get("title") or "" for item in (preference or {}).get("decisions") or [] if item.get("title"))
    basis = f", based on earlier decisions: {titles}" if titles else ""
    relation = RELATION_BY_ID.get((preference or {}).get("relation") or "")
    if status == "applied" and relation:
        return f"This analysis looks more closely at {relation['label']}{basis}."
    if status == "applied" and (preference or {}).get("criterion") in CRITERIA:
        return f"This analysis looks more closely at {CRITERIA[preference['criterion']]['label']}{basis}."
    criteria = (preference or {}).get("criteria") or []
    if status == "uncertain" and len(criteria) == 2 and all(criterion in CRITERIA for criterion in criteria):
        first, second = (CRITERIA[criterion]["label"] for criterion in criteria)
        return f"Earlier decisions weighed {first} and {second} differently, so each option compares both."
    return None
