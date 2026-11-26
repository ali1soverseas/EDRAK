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
from typing import Any, Iterable, Optional

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


# ------------------------------------------------------------------------------------------------------------
# Ask Edrak, any scope: answers cite tags from a SourceBook (see edrak.chat.sources).

CHAT_LABELS = ("brief", "workspace", "company", "documents", "web_unverified", "conversation", "not_covered", "declined")
# Which label wins when an answer cites several kinds of source. Anything from the web makes the whole answer unverified.
_PRECEDENCE = (("web", "web_unverified"), ("brief", "brief"), ("workspace", "workspace"), ("company", "company"), ("documents", "documents"))
_GROUNDED = {"brief", "workspace", "company", "documents"}
# Text that only appears in Ask Edrak's own instructions; an answer that repeats it is leaking the prompt.
_PROMPT_MARKERS = ("TOOL RESULTS SO FAR", "You must answer now", "Reply with only a JSON object", "<untrusted_source> tags",
                   "Tools (one per step", "CONVERSATION SO FAR:")
_SMALL_INT = re.compile(r"^(?:\d|1[0-2])$")  # a bare count up to 12 ("3 competitors", "top 5")
_ALLOWED_CHAT_RECOMMENDATIONS = {
    "recommend launching", "recommend entering", "recommend against", "we recommend", "i recommend", "our recommendation",
    "ننصح بإطلاق", "ننصح بدخول", "نوصي بإطلاق", "نوصي بدخول", "لا ننصح بدخول", "لا ننصح بإطلاق",
}


@dataclass
class ChatAnswerCheck:
    answer: str
    label: str
    kinds: list[str]
    tags: list[str]
    evidence_nos: list[int]
    metric_uids: list[str]
    removed_numbers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)


def _soften(text: str) -> tuple[str, bool]:
    """Remove categorical verdicts, while allowing qualified recommendations in chat."""
    found = [phrase for phrase in verdict_language(text) if phrase not in _ALLOWED_CHAT_RECOMMENDATIONS]
    if not found:
        return text, False
    for phrase in found:
        text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:])", r"\1", text)
    return text.strip(), True


def check_chat_answer(answer: str, claimed_label: Optional[str], *, book: Any, cited: Iterable[str] = (),
                      evidence_nos: Iterable[int] = (), metric_uids: Iterable[str] = (),
                      brief_metrics: Optional[dict[str, dict[str, Any]]] = None, web_used: bool = False,
                      user_text: str = "", lang: str = "en") -> ChatAnswerCheck:
    """Check an Ask Edrak answer before the person sees it.

    * Citations must be tags in `book`; others are removed. "E8" without brackets counts as [E8].
    * The label comes from the kinds of sources cited: web > brief > workspace > company > documents.
      An answer that claims a source but cites none is "not covered"; small talk and clarifying questions are "conversation".
    * Every number must be a stored metric the answer relies on, appear in a cited source, appear in what the person
      wrote, be a year, or be a bare count up to 12. Others are replaced by "(figure removed)".
    * Ranking and verdict wording is softened, personal data is redacted, and a reply that repeats Ask Edrak's
      own instructions is withheld.
    """
    from edrak.chat.sources import CITE_RE, kind_of, split_groups
    from edrak.guardrails.patterns import redact_pii

    brief_metrics = brief_metrics or {}
    notes: list[str] = []
    checks: list[dict[str, Any]] = []
    text = split_groups(answer or "").strip()

    if any(marker.lower() in text.lower() for marker in _PROMPT_MARKERS):
        checks.append({"name": "chat_prompt_leak", "action": "blocked"})
        msg = ("I can't share how I'm set up, but I'm happy to answer questions about your business."
               if lang != "ar" else "لا أستطيع مشاركة طريقة إعدادي، لكن يسعدني الإجابة عن أسئلة عملك.")
        return ChatAnswerCheck(msg, "conversation", [], [], [], [], notes=[], checks=checks)

    # Bare "E8" -> "[E8]" when E8 is a source of this chat.
    text = _BARE_CITE.sub(lambda m: f"[E{m.group(1)}]" if f"E{m.group(1)}" in book else m.group(0), text)

    unknown: list[str] = []
    # If a claim's only citation is unknown, remove that claim rather than leaving it uncited.
    # Keep a claim with another valid citation, but still remove its invalid citation tag below.
    claim_blocks = re.split(r"(?m)(?=^\s*(?:[-*+]\s+|\d+[.)]\s+))", text)
    safe_blocks: list[str] = []
    for block in claim_blocks:
        tags_in_block = CITE_RE.findall(block)
        missing = [tag for tag in tags_in_block if tag not in book]
        if missing:
            unknown.extend(missing)
            if not any(tag in book for tag in tags_in_block):
                continue
        safe_blocks.append(block)
    text = "".join(safe_blocks)

    def keep_tag(m: re.Match) -> str:
        if m.group(1) in book:
            return m.group(0)
        if m.group(1) not in unknown:
            unknown.append(m.group(1))
        return ""

    text = CITE_RE.sub(keep_tag, text)
    if unknown:
        notes.append("Some citations did not match a source Edrak read and were removed; claims supported only by those citations were also removed.")
        checks.append({"name": "chat_unknown_citation", "action": "redacted", "tags": unknown[:10]})

    tags = list(dict.fromkeys([*CITE_RE.findall(text), *[t for t in cited if t in book],
                               *[f"E{n}" for n in evidence_nos if f"E{n}" in book]]))
    uids = [u for u in metric_uids if u in brief_metrics]
    kinds = list(dict.fromkeys(kind_of(t) for t in tags))
    if uids and "brief" not in kinds:
        kinds.insert(0, "brief")

    claimed = claimed_label if claimed_label in CHAT_LABELS else None
    label = next((lab for kind, lab in _PRECEDENCE if kind in kinds), None)
    if label is None:
        if web_used and claimed != "conversation":
            label = "web_unverified"     # it read the web and cites nothing else: what it says rests on the web
        elif claimed == "conversation":
            label = "conversation"
        else:
            label = "not_covered"
            if claimed in _GROUNDED:
                notes.append("No source Edrak holds supports this, so it is marked not covered.")
    if label == "web_unverified" and any(k in kinds for k in _GROUNDED):
        notes.append("Edrak's own sources are cited next to web results; the web part is unverified.")

    # Numbers.
    allowed = _allowed_values(brief_metrics[u] for u in uids) | _allowed_values(brief_metrics.values() if label == "brief" else [])
    source_text = book.text_of(tags) + " " + user_text
    if label == "web_unverified":
        source_text += " " + book.text_of([t for t in book.items if kind_of(t) == "web"])
    in_source = {round(p.value, 2) for p in parse_numbers(source_text)}
    # The bare figure as written, too: "3.2 billion" in a source parses as 3.2e9, but the answer's "3.2" is read on its own.
    in_source |= {round(float(n.replace(",", "")), 2) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", source_text) if n.replace(",", "").replace(".", "", 1).isdigit()}
    removed: list[str] = []

    def keep(match: re.Match) -> str:
        raw = match.group(0)
        parsed = parse_numbers(raw)
        if not parsed:
            return raw
        v = round(parsed[0].value, 2)
        if v in allowed or v in in_source or round(parsed[0].value, 1) in allowed or (v == int(v) and 1900 <= v <= 2100):
            return raw
        if _SMALL_INT.match(raw.strip()):
            return raw
        removed.append(raw.strip())
        return "(figure removed)" if lang != "ar" else "(حُذف رقم)"

    # Hide citation tags (they contain digits) while numbers are checked.
    hidden: list[str] = []

    def hide(m: re.Match) -> str:
        hidden.append(m.group(0))
        return f"\x00{len(hidden) - 1:x}\x01".translate(str.maketrans("0123456789abcdef", "ghijklmnopqrstuv"))

    masked = CITE_RE.sub(hide, text)
    cleaned = _NUMBER.sub(keep, masked)
    cleaned = re.sub("\x00([g-v]+)\x01", lambda m: hidden[int(m.group(1).translate(str.maketrans("ghijklmnopqrstuv", "0123456789abcdef")), 16)], cleaned)
    if removed:
        notes.append("A number that Edrak could not trace to a source was removed.")
        checks.append({"name": "chat_untraced_number", "action": "redacted", "values": removed[:10]})

    cleaned, softened = _soften(cleaned)
    if softened:
        notes.append("Definitive or ranking wording was softened; evidence-grounded suggestions may remain for you to evaluate.")
        checks.append({"name": "chat_verdict_language", "action": "redacted"})

    cleaned, pii = redact_pii(cleaned)
    if pii:
        checks.append({"name": "chat_output_pii", "action": "redacted", "kinds": pii})

    if label == "web_unverified" and "unverified" not in cleaned.lower() and "غير موثّق" not in cleaned and "غير موثق" not in cleaned:
        cleaned = cleaned.rstrip() + ("\n\n(From the web, unverified.)" if lang != "ar" else "\n\n(من الويب، غير موثّق.)")
    if not cleaned.strip():
        cleaned = "I could not produce an answer I can stand behind. Try rephrasing the question." if lang != "ar" else \
            "لم أتمكن من صياغة إجابة موثوقة. جرّب إعادة صياغة السؤال."
        label = "not_covered"

    e_nos = sorted({int(t[1:]) for t in tags if re.fullmatch(r"E\d+", t)})
    return ChatAnswerCheck(answer=cleaned, label=label, kinds=kinds, tags=tags, evidence_nos=e_nos, metric_uids=uids,
                           removed_numbers=removed, notes=notes, checks=checks)
