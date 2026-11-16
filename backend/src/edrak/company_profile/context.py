"""Pack source documents into one labeled block for a single model call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from edrak.company_profile.models import SourceDocument, SourceRef
from edrak.core.config import settings
from edrak.guardrails.content import is_high_risk, sanitize, wrap

TRUNCATION_MARK = "\n[truncated]"
SEPARATOR = "\n\n"
_LABEL_CHARS = 200
_CLOSING_TAG = re.compile(r"</\s*untrusted_source[^>]*>?", re.IGNORECASE)


@dataclass
class PackedContext:
    text: str = ""
    sources: list[SourceRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    char_count: int = 0  # characters of source text that made it into `text`


def _label(value: str) -> str:
    """A ref or title on one line, without instruction-like text, and not longer than a label should be."""
    return sanitize(" ".join(value.split()))[0][:_LABEL_CHARS]


def _block(source_id: str, doc: SourceDocument, body: str) -> str:
    header = f"[{source_id}] type={doc.source_type.value} ref={_label(doc.ref)} title={_label(doc.title)}\n"
    return header + wrap(body, source_id)


def pack_context(documents: Sequence[SourceDocument], budget: Optional[int] = None) -> PackedContext:
    """Render `documents` as labeled blocks S1..Sn, lowest priority number first, within `budget` characters.

    Each block is the id, type, ref and title, then the text between untrusted-source markers. Text
    is sanitized first, and a page that is mostly instructions is left out. The block that crosses
    the budget is cut rather than dropped; whatever comes after it is not sent, and a warning says so.
    """
    limit = settings.PROFILE_IMPORT_CONTEXT_CHARS if budget is None else budget
    ordered = sorted(documents, key=lambda d: d.priority)  # stable: equal priorities keep their order
    packed = PackedContext()
    blocks: list[str] = []
    used = 0
    empty = risky = removed_lines = 0
    omitted = 0
    cut = False

    for index, doc in enumerate(ordered):
        if not doc.text.strip():
            empty += 1
            continue
        if is_high_risk(doc.text):
            risky += 1
            continue
        body, removed = sanitize(doc.text.strip())
        body = _CLOSING_TAG.sub("", body)
        removed_lines += removed

        source_id = f"S{len(packed.sources) + 1}"
        gap = len(SEPARATOR) if blocks else 0
        block = _block(source_id, doc, body)
        if used + gap + len(block) <= limit:
            kept = body
        else:
            # Over budget: keep as much of this block's text as the labels and markers leave room for.
            frame = gap + len(_block(source_id, doc, "")) + len(TRUNCATION_MARK)
            room = limit - used - frame
            cut = True
            omitted = sum(1 for later in ordered[index + 1 :] if later.text.strip())
            if room <= 0:
                omitted += 1  # not even its labels fit, so this one is not sent either
                break
            kept = body[:room].rstrip() + TRUNCATION_MARK
            block = _block(source_id, doc, kept)
            kept = kept[: -len(TRUNCATION_MARK)]

        blocks.append(block)
        used += gap + len(block)
        packed.char_count += len(kept)
        packed.sources.append(SourceRef(id=source_id, type=doc.source_type, ref=doc.ref, title=doc.title))
        if cut:
            break

    if cut:
        packed.warnings.append("The sources were longer than the size limit, so some text was cut.")
    if omitted:
        packed.warnings.append(f"{omitted} lower-priority source(s) did not fit and were not read.")
    if empty:
        packed.warnings.append(f"{empty} source(s) had no readable text and were left out.")
    if risky:
        packed.warnings.append(f"{risky} source(s) were mostly instruction-like text and were left out.")
    if removed_lines:
        packed.warnings.append(f"{removed_lines} instruction-like line(s) were removed from the sources.")

    packed.text = SEPARATOR.join(blocks)
    return packed
