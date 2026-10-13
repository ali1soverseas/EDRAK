"""The model's part of the brief: headlines, labels and why a visual was chosen. It never writes a number.

It sees metric NAMES, tokens and qualitative facts computed by code (direction, status), never
values. A number can only appear as a token `{m:<uid>}` that code replaces from the metrics table.
Any text with a digit outside a token is rejected and replaced by a plain fallback.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from edrak.metrics.format import format_value

logger = logging.getLogger(__name__)

TOKEN = re.compile(r"\{m:([0-9a-f]{32})\}")
DIGIT = re.compile(r"[0-9٠-٩۰-۹]")
MAX_CARDS_IN_PROMPT = 60
SIMILAR = 0.6  # word overlap above which a "why it matters" just repeats other text

# Words that make a claim about a metric. A headline may only use them when that metric has a usable
# figure or a supplied signal talks about it; otherwise the claim has nothing behind it.
DIMENSION_WORDS: dict[str, tuple[str, ...]] = {
    "price": ("price", "pricing", "priced", "cheaper", "pricier", "expensive", "premium", "discount"),
    "customer voice": ("customer voice", "sentiment", "customers say", "reviews", "rating", "share of voice", "complain", "perception"),
    "customer base": ("subscribers", "customer base", "user base", "paying customers"),
    "revenue": ("revenue", "arr", "sales"),
    "market size": ("market size", "market worth", "large market", "sizeable market"),
    "growth": ("growth", "growing", "cagr", "expanding"),
    "adoption": ("adoption", "adopting"),
    "regulation": ("regulation", "regulatory", "licence", "license", "law"),
    "capacity": ("capacity", "ready to ship", "can deliver"),
    "funding": ("funding", "raised", "investors"),
}
METRIC_DIMENSION = {
    "price_per_tier": "price", "own_price": "price", "effective_price_usage": "price",
    "sentiment_split": "customer voice", "rating": "customer voice", "share_of_voice": "customer voice",
    "top_pain_points": "customer voice", "switching_intent": "customer voice",
    "paid_subscribers": "customer base", "revenue_arr": "revenue", "market_size": "market size",
    "market_cagr": "growth", "segment_growth": "growth", "adoption_rate": "adoption",
    "regulation_events": "regulation", "capacity": "capacity", "funding_total": "funding",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z\u0600-\u06ff']+", (text or "").lower()) if len(w) > 3}


def overlap(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _mentions(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", (text or "").lower()) is not None


def unmet_dimensions(found_metric_ids: set[str], required_metric_ids: list[str], signals: list[dict[str, Any]]) -> list[str]:
    """Dimensions the template asks about that have no usable figure and no signal speaking to them."""
    said = " ".join(" ".join(str(s.get(k) or "") for k in ("title", "signal", "interpretation")) for s in signals).lower()
    covered = {METRIC_DIMENSION[m] for m in found_metric_ids if m in METRIC_DIMENSION}
    out = []
    for m in required_metric_ids:
        dim = METRIC_DIMENSION.get(m)
        if not dim or dim in covered or dim in out:
            continue
        if any(_mentions(said, w) for w in DIMENSION_WORDS[dim]):
            continue
        out.append(dim)
    return out


def ungrounded(headline: str, unmet: list[str]) -> list[str]:
    """The unmet dimensions a headline still makes a claim about."""
    return [d for d in unmet if any(_mentions(headline, w) for w in DIMENSION_WORDS.get(d, ()))]


class CardCopy(BaseModel):
    card_key: str
    headline: str = Field(description="One short line saying what this card shows. Numbers only as {m:uid} tokens.")
    why_visual: str = Field(description="One short line: why this visual fits this data. No numbers.")


class SignalCopy(BaseModel):
    signal_id: str
    why_it_matters: str = Field(description="One sentence naming who is affected and what to watch. Numbers only as tokens.")


class ProposedCopy(BaseModel):
    headline: str = Field(description="The one-sentence insight for the whole brief. Numbers only as {m:uid} tokens.")
    cards: list[CardCopy] = Field(default_factory=list)
    signals: list[SignalCopy] = Field(default_factory=list)


STATUS_WORDS = re.compile(r"\b(?:thin|unverified|verified|conflict|gap)\b", re.I)


def misattributed(text: str, metrics: dict[str, dict[str, Any]], names: list[str]) -> bool:
    """True when a figure is credited to the wrong company: "NVIDIA's unit price {m:x}" where x is AMD's.

    A figure is misattributed when other companies are named just before it and its own entity is not.
    """
    for m in TOKEN.finditer(text):
        entity = str((metrics.get(m.group(1)) or {}).get("entity") or "").lower()
        before = text[max(0, m.start() - 80):m.start()].lower()
        others = [n.lower() for n in names if n and n.lower() in before and n.lower() not in entity and entity not in n.lower()]
        if entity and others and entity not in before:
            return True
    return False


def render_text(text: str, metrics: dict[str, dict[str, Any]], names: list[str], *, need_entity: bool = False) -> Optional[str]:
    """Replace tokens with stored values. None when the text is not allowed."""
    text = (text or "").strip()
    if not text:
        return None
    for uid in TOKEN.findall(text):
        if uid not in metrics:
            return None
    bare = TOKEN.sub("", text)
    if DIGIT.search(bare) or STATUS_WORDS.search(bare):
        return None  # numbers only come from tokens; evidence labels ("thin") are not prose
    if misattributed(text, metrics, names):
        return None
    if need_entity and not (TOKEN.search(text) or any(n and n.lower() in text.lower() for n in names)):
        return None
    return TOKEN.sub(lambda m: format_value(metrics[m.group(1)]["value"], metrics[m.group(1)]["unit"]), text)


def _facts(card: dict[str, Any]) -> str:
    slots = card.get("slots") or {}
    bits = [f"status={card['status']}"]
    comp = slots.get("comparator")
    if comp:
        bits.append(f"{comp['label']}: {'higher' if comp['direction'] > 0 else 'lower' if comp['direction'] < 0 else 'same'}")
    if card["component"] == "ladder" and slots.get("rows"):
        bits.append(f"highest: {slots['rows'][0]['entity']}; lowest: {slots['rows'][-1]['entity']}")
    if card["component"] == "segmented" and slots.get("parts"):
        bits.append(f"largest part: {slots['parts'][0]['label']}")
    if card["component"] == "table":
        bits.append("shown as a table (not enough data for a chart)")
    return ", ".join(bits)


def _prompt(cards: list[dict[str, Any]], signals: list[dict[str, Any]], framing: str, goal: str, names: list[str], strip: list[dict[str, Any]],
            unmet: list[str] = (), feedback: str = "") -> str:
    lines = []
    for c in cards[:MAX_CARDS_IN_PROMPT]:
        tokens = ", ".join(f"{{m:{u}}}" for u in c["metric_uids"][:2])
        lines.append(f"- key={c['card_key']} | {c['title']} | component={c['component']} | {_facts(c)} | tokens: {tokens or 'none'}")
    sigs = "\n".join(f"- id={s['signal_id']} | {s.get('title','')} | {s.get('signal','')} | {s.get('interpretation','')}" for s in signals[:6])
    top = "\n".join(f"- {s['label']} ({s['entity'] or 'company'}): {{m:{s['uid']}}} | {s['facts']}" for s in strip[:8])
    return (
        "You write the words on a decision brief. All figures are already computed and checked by code; you never see or write one.\n"
        "RULES: Never type a digit. To mention a figure write its token exactly, e.g. {m:abc...}. Do not invent facts. "
        "Do not rank options or say what to choose. Name real companies or markets, not 'the company'. Keep every line short.\n"
        f"Business goal: {goal}\nNames in scope: {', '.join(names)}\nHeadline framing: {framing}\n"
        "The headline states the single most important thing the evidence below shows. It is an insight, not a summary of "
        "the work and not advice.\n"
        f"NOT FOUND (say nothing about these in the headline or anywhere): {', '.join(unmet) or 'none'}\n"
        "why_it_matters names who is affected and the concrete consequence to watch. It must not restate the signal or "
        "repeat another signal's text.\n" + (f"{feedback}\n" if feedback else "") + "\n"
        f"KEY FIGURES (use their tokens in the headline):\n{top or '- none verified yet'}\n\n"
        f"SIGNALS (write why_it_matters for each, id as given):\n{sigs or '- none'}\n\n"
        f"CARDS (write headline and why_visual for each):\n" + "\n".join(lines)
    )


def propose_copy(cards: list[dict[str, Any]], signals: list[dict[str, Any]], strip: list[dict[str, Any]], *, framing: str, goal: str,
                 names: list[str], unmet: list[str] = (), feedback: str = "") -> Optional[ProposedCopy]:
    from edrak.core.llm import get_structured, schema_instruction

    try:
        model = get_structured(ProposedCopy, agent="orchestrator", temperature=0.2)
        return model.invoke([_prompt(cards, signals, framing, goal, names, strip, unmet, feedback), schema_instruction(ProposedCopy)])
    except Exception as exc:  # noqa: BLE001
        logger.info("Brief copy proposal failed: %s", exc)
        return None


def apply_copy(copy: Optional[ProposedCopy], cards: list[dict[str, Any]], signals: list[dict[str, Any]], metrics: dict[str, dict[str, Any]],
               names: list[str], unmet: list[str] = ()) -> tuple[Optional[str], list[str]]:
    """Validate the model's words and attach what passes. Returns (headline or None, rejected card keys)."""
    from edrak.metrics.registry import get_registry

    reg = get_registry()
    rejected: list[str] = []
    by_card = {c.card_key: c for c in (copy.cards if copy else [])}
    for card in cards:
        got = by_card.get(card["card_key"])
        headline = render_text(got.headline, metrics, names) if got else None
        why = render_text(got.why_visual, metrics, names) if got else None
        if got and headline is None:
            rejected.append(card["card_key"])
        defn = reg.get(card.get("metric_id"))
        card["headline"] = headline or card["title"]
        card["why_visual"] = why or (f"{defn.visual} is the fixed visual for {defn.label()}." if defn else None)
    by_sig = {s.signal_id: s for s in (copy.signals if copy else [])}
    written: list[str] = []
    for sig in signals:
        got = by_sig.get(sig["signal_id"])
        text = render_text(got.why_it_matters, metrics, names, need_entity=True) if got else None
        own = [sig.get("signal") or "", sig.get("title") or "", *(sig.get("implications") or [])]
        if text and (any(overlap(text, o) >= SIMILAR for o in own) or any(overlap(text, w) >= SIMILAR for w in written)):
            text = None  # generic: it repeats the signal or another card
        if text is None:
            # The interpretation is the cross-signal stage's own reading; the implications are generic.
            interp = sig.get("interpretation") or ""
            if interp and not DIGIT.search(interp) and all(overlap(interp, w) < SIMILAR for w in written):
                text = interp
        if text:
            written.append(text)
        sig["why_it_matters"] = text
    headline = render_text(copy.headline, metrics, names, need_entity=True) if copy else None
    if headline and ungrounded(headline, list(unmet)):
        headline = None  # claims something about a dimension with no figure and no signal behind it
    return headline, rejected
