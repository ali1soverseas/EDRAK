"""Guardrail on a chat answer, applied after the model writes it and before the person sees it.

* Every number in the answer must equal a stored metric value or appear in the evidence the answer cites;
  one that does not is removed and the answer says so.
* Evidence numbers and metric ids the answer cites must belong to this brief.
* Web answers are always labelled unverified; an answer that cites neither the brief nor the web is "not covered".
* Verdict language about options is softened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from edrak.guardrails.output import _strip_phrases, verdict_language
from edrak.metrics.numbers import parse_numbers

LABELS = ("brief", "web_unverified", "not_covered")
_CITE = re.compile(r"\[E(\d+)\]")
# A figure stands on its own: digits glued to letters are part of a name ("3.5D XDSiP", "Q2", "H100") and are left alone.
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])[$€£]?\s?\d[\d,]*(?:\.\d+)?(?:\s?%|\s?[kKmMbB]\b)?(?![A-Za-z0-9]|[.,]\d)")
_BARE_CITE = re.compile(r"(?<![\[\w])E(\d{1,3})\b")


@dataclass
class ChatCheck:
    answer: str
    label: str
    evidence_nos: list[int]
    metric_uids: list[str]
    removed_numbers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _allowed_values(metrics: Iterable[dict[str, Any]]) -> set[float]:
    vals: set[float] = set()
    for m in metrics:
        v = m.get("value")
        if isinstance(v, (int, float)):
            vals.add(round(float(v), 2))
            for scale in (1e3, 1e6, 1e9, 1e12):
                if abs(v) >= scale:
                    vals.add(round(float(v) / scale, 1))
    return vals


def check_answer(answer: str, label: str, *, evidence_nos: list[int], metric_uids: list[str], brief_evidence_nos: set[int],
                 brief_metrics: dict[str, dict[str, Any]], evidence_text: dict[int, str], web_text: str = "") -> ChatCheck:
    notes: list[str] = []
    label = label if label in LABELS else "not_covered"
    # "E8" written without brackets is still a citation of this brief's source 8.
    answer = _BARE_CITE.sub(lambda m: f"[E{m.group(1)}]" if int(m.group(1)) in brief_evidence_nos else m.group(0), answer)
    cited_nos = sorted({n for n in evidence_nos if n in brief_evidence_nos} | {int(n) for n in _CITE.findall(answer) if int(n) in brief_evidence_nos})
    uids = [u for u in metric_uids if u in brief_metrics]
    if len(cited_nos) != len(set(evidence_nos)) and evidence_nos:
        notes.append("Some cited sources are not part of this brief and were dropped.")
    # The answer may only be "from the brief" if it cites the brief.
    if label == "brief" and not cited_nos and not uids:
        label = "not_covered"
        notes.append("No source in the brief supports this, so it is marked not covered.")
    if label == "web_unverified" and cited_nos:
        notes.append("Brief sources are cited next to web results; the web part is unverified.")

    allowed = _allowed_values(brief_metrics[u] for u in uids) | _allowed_values(brief_metrics.values() if label == "brief" else [])
    source = " ".join(evidence_text.get(n, "") for n in cited_nos) + (" " + web_text if label == "web_unverified" else "")
    in_source = {round(p.value, 2) for p in parse_numbers(source)}
    removed: list[str] = []

    def keep(match: re.Match) -> str:
        parsed = parse_numbers(match.group(0))
        if not parsed:
            return match.group(0)
        v = round(parsed[0].value, 2)
        if v in allowed or v in in_source or round(parsed[0].value, 1) in allowed or (v == int(v) and 1900 <= v <= 2100):
            return match.group(0)
        removed.append(match.group(0).strip())
        return "(figure removed)"

    # Citations like [E12] are digits too; hide them (digits become letters) while the numbers are checked.
    masked = _CITE.sub(lambda m: "\x00" + "".join(chr(97 + int(d)) for d in m.group(1)) + "\x01", answer)
    cleaned = _NUMBER.sub(keep, masked)
    cleaned = re.sub("\x00([a-j]+)\x01", lambda m: "[E" + "".join(str(ord(c) - 97) for c in m.group(1)) + "]", cleaned)
    if removed:
        notes.append("A number that is not in the brief's stored metrics or cited sources was removed.")
    found = verdict_language(cleaned)
    if found:
        cleaned = _strip_phrases(cleaned, found) or cleaned
        notes.append("Recommendation wording about the options was softened; the brief does not rank options.")
    if label == "web_unverified" and "unverified" not in cleaned.lower():
        cleaned = cleaned.rstrip() + "\n\n(From the web, unverified.)"
    return ChatCheck(answer=cleaned, label=label, evidence_nos=cited_nos, metric_uids=uids, removed_numbers=removed, notes=notes)
