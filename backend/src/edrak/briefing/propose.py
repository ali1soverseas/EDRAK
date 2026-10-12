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


def render_text(text: str, metrics: dict[str, dict[str, Any]], names: list[str], *, need_entity: bool = False) -> Optional[str]:
    """Replace tokens with stored values. None when the text is not allowed."""
    text = (text or "").strip()
    if not text:
        return None
    for uid in TOKEN.findall(text):
        if uid not in metrics:
            return None
    bare = TOKEN.sub("", text)
    if DIGIT.search(bare):
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


def _prompt(cards: list[dict[str, Any]], signals: list[dict[str, Any]], framing: str, goal: str, names: list[str], strip: list[dict[str, Any]]) -> str:
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
        f"Business goal: {goal}\nNames in scope: {', '.join(names)}\nHeadline framing: {framing}\n\n"
        f"KEY FIGURES (use their tokens in the headline):\n{top or '- none verified yet'}\n\n"
        f"SIGNALS (write why_it_matters for each, id as given):\n{sigs or '- none'}\n\n"
        f"CARDS (write headline and why_visual for each):\n" + "\n".join(lines)
    )


def propose_copy(cards: list[dict[str, Any]], signals: list[dict[str, Any]], strip: list[dict[str, Any]], *, framing: str, goal: str,
                 names: list[str]) -> Optional[ProposedCopy]:
    from edrak.core.llm import get_structured, schema_instruction

    try:
        model = get_structured(ProposedCopy, agent="orchestrator", temperature=0.2)
        return model.invoke([_prompt(cards, signals, framing, goal, names, strip), schema_instruction(ProposedCopy)])
    except Exception as exc:  # noqa: BLE001
        logger.info("Brief copy proposal failed: %s", exc)
        return None


def apply_copy(copy: Optional[ProposedCopy], cards: list[dict[str, Any]], signals: list[dict[str, Any]], metrics: dict[str, dict[str, Any]],
               names: list[str]) -> tuple[Optional[str], list[str]]:
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
    for sig in signals:
        got = by_sig.get(sig["signal_id"])
        text = render_text(got.why_it_matters, metrics, names, need_entity=True) if got else None
        if text is None:
            # Fall back to the implication the cross-signal stage already wrote, if it has no number in it.
            for imp in sig.get("implications") or []:
                if imp and not DIGIT.search(imp):
                    text = imp
                    break
        sig["why_it_matters"] = text
    headline = render_text(copy.headline, metrics, names, need_entity=True) if copy else None
    return headline, rejected
