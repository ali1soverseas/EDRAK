"""Guardrail on a brief before it is saved.

The brief informs a decision; it does not make one. Checked:
  * every finding cites evidence; one that does not is marked uncited (the brief shows it as such);
  * every number in a finding appears in the evidence it cites; one that does not gets a limitation;
  * options carry no ranking or verdict language ("we recommend", "the best option", ...); it is
    removed from the text, and options stay unranked;
  * the summary carries no verdict language; such a sentence is removed.

check_brief returns the document with a `guardrails` section listing what was found and changed.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

_VERDICT_FILE = Path(__file__).resolve().parents[1] / "agents" / "customer_trends" / "config" / "verdict_phrases.yaml"
_RANKING = (
    "the best option", "best option", "the winner", "clearly superior", "the right choice", "the only option",
    "you should", "we suggest", "we advise", "top choice", "number one choice", "obvious choice",
)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\s?%)?")


@functools.lru_cache(maxsize=1)
def verdict_phrases() -> tuple[str, ...]:
    try:
        data = yaml.safe_load(_VERDICT_FILE.read_text(encoding="utf-8")) or {}
        phrases = [str(p).lower() for p in data.get("phrases", [])]
    except OSError:
        phrases = []
    return tuple(dict.fromkeys([*phrases, *_RANKING]))


def verdict_language(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [p for p in verdict_phrases() if p in lowered]


def _strip_phrases(text: str, phrases: list[str]) -> str:
    for phrase in phrases:
        text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip(" ,.;:") + ("." if text.strip().endswith(".") else "")


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "").replace(" ", "") for n in _NUMBER.findall(text or "")]


def _evidence_text(document: dict[str, Any]) -> dict[int, str]:
    out: dict[int, str] = {}
    for item in document.get("evidence") or []:
        ev = item.get("evidence") or {}
        text = " ".join(str(ev.get(k) or "") for k in ("extracted_fact", "excerpt", "source_title"))
        out[int(item.get("display_no") or 0)] = text.replace(",", "")
    return out


def _metric_numbers(document: dict[str, Any]) -> set[float]:
    """Every figure the stored metrics hold, in the forms the screen may show them."""
    vals: set[float] = set()
    for m in (document.get("metrics") or {}).values():
        v = m.get("value")
        if isinstance(v, (int, float)):
            vals.add(round(float(v), 2))
            for scale in (1e3, 1e6, 1e9, 1e12):
                if abs(v) >= scale:
                    vals.add(round(float(v) / scale, 1))
    return vals


def _check_typed(document: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Cards, headline and signal text: every number must be a stored metric, every card must be sourced,
    and nothing may sound like a verdict."""
    if document.get("cards") is None:
        return
    from edrak.metrics.numbers import parse_numbers

    known = _metric_numbers(document)

    def stray(text: str) -> list[str]:
        out = []
        for p in parse_numbers(text or ""):
            if round(p.value, 2) in known or round(p.value, 1) in known or (re.fullmatch(r"(19|20)\d{2}", str(int(p.value))) if p.value == int(p.value) else False):
                continue
            out.append(p.text)
        return out

    for key, card in (document.get("cards") or {}).items():
        if card.get("component") != "gap" and not (card.get("refs") or card.get("metric_uids") or (card.get("slots") or {}).get("data_source")):
            card["caveat"] = card.get("caveat") or "No source is attached to this card."
            card["status"] = "thin" if card.get("status") == "verified" else card.get("status")
            checks.append({"check": "uncited_card", "action": "flagged", "card": key})
        for field in ("headline", "why_visual"):
            text = card.get(field) or ""
            bad = stray(text)
            if bad:
                card[field] = card.get("title") if field == "headline" else None
                checks.append({"check": "card_number_not_in_table", "action": "redacted", "card": key, "numbers": bad[:4]})
            found = verdict_language(text)
            if found:
                card[field] = _strip_phrases(text, found) or card.get("title")
                checks.append({"check": "verdict_language", "action": "redacted", "where": f"card {key}", "phrases": found})

    headline = document.get("headline") or ""
    bad = stray(headline)
    if bad or verdict_language(headline):
        checks.append({"check": "headline_rewritten", "action": "redacted", "numbers": bad[:4]})
        sig = next((s.get("title") for s in document.get("signals_view") or [] if s.get("title")), None)
        document["headline"] = sig or document.get("question") or headline
        document["summary_text"] = document["headline"]
    for sig in document.get("signals_view") or []:
        text = sig.get("why_it_matters") or ""
        if text and (stray(text) or verdict_language(text)):
            sig["why_it_matters"] = None
            checks.append({"check": "why_it_matters_dropped", "action": "redacted", "signal": sig.get("signal_id")})


def check_brief(document: dict[str, Any]) -> dict[str, Any]:
    """Check (and minimally correct) a brief document. Returns the same document."""
    checks: list[dict[str, Any]] = []
    evidence = _evidence_text(document)

    for lens in document.get("lenses") or []:
        for item in lens.get("findings") or []:
            finding = item.get("finding") or {}
            statement = finding.get("statement") or ""
            refs = [r.get("display_no") for r in item.get("refs") or [] if r.get("display_no")]
            if not refs:
                item["guardrail"] = "uncited"
                checks.append({"check": "uncited_claim", "action": "flagged", "worker": lens.get("worker"),
                               "text": statement[:160]})
                continue
            cited = " ".join(evidence.get(int(n), "") for n in refs)
            missing = [n for n in _numbers(statement) if n.rstrip("%") not in cited and n not in cited]
            # Years and small ordinals are usually context, not claims.
            missing = [n for n in missing if not re.fullmatch(r"(19|20)\d{2}|[0-9]", n)]
            if missing:
                item["guardrail"] = "unsupported_number"
                limitations = list(finding.get("limitations") or [])
                note = f"The figure(s) {', '.join(missing[:3])} were not found in the cited sources."
                if note not in limitations:
                    limitations.append(note)
                finding["limitations"] = limitations
                checks.append({"check": "unsupported_number", "action": "flagged", "worker": lens.get("worker"),
                               "numbers": missing[:5], "text": statement[:160]})

    for option in document.get("options") or []:
        if option.get("key") and not option.get("supporting_signal_ids") and document.get("cards") is not None:
            checks.append({"check": "uncited_option", "action": "flagged", "where": f"option {option.get('key')}"})
            option["guardrail"] = "uncited"
        for field in ("title", "description", "body", "thesis", "would_change_if", "cost_of_waiting"):
            text = option.get(field)
            if not isinstance(text, str):
                continue
            found = verdict_language(text)
            if found:
                option[field] = _strip_phrases(text, found) or text
                checks.append({"check": "verdict_language", "action": "redacted", "where": f"option {option.get('key')}",
                               "phrases": found})
        option.pop("rank", None)
        option.pop("recommended", None)

    summary = document.get("summary")
    if isinstance(summary, dict):
        for part, segments in summary.items():
            if not isinstance(segments, list):
                continue
            kept = []
            for seg in segments:
                found = verdict_language(seg.get("text", "")) if isinstance(seg, dict) else []
                if found:
                    checks.append({"check": "verdict_language", "action": "redacted", "where": f"summary {part}",
                                   "phrases": found})
                    continue
                kept.append(seg)
            summary[part] = kept

    _check_typed(document, checks)

    document["guardrails"] = {
        "checked": True,
        "issues": len(checks),
        "checks": checks[:100],
    }
    return document
