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
        for field in ("title", "description", "body"):
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

    document["guardrails"] = {
        "checked": True,
        "issues": len(checks),
        "checks": checks[:100],
    }
    return document
