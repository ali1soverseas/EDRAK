"""Guardrail on uploaded documents, chunk by chunk, before anything is indexed.

A chunk that tries to instruct the model (prompt injection, or text hidden with invisible
characters) is flagged and excluded from retrieval: it is kept in document_chunks so the file
viewer can show why, but it never reaches the vector store or a prompt. Personal data is flagged
so the person knows it is there; it stays searchable because internal documents legitimately
hold customer records. The pipeline quarantines a document when more than 30% of it is excluded.
"""

from __future__ import annotations

from edrak.guardrails.patterns import find_pii, hidden_char_count, injection_hits


def scan_chunk(text: str) -> tuple[list[str], bool]:
    """(flags, exclude) for one chunk of a document."""
    flags: list[str] = []
    exclude = False
    hits = injection_hits(text)
    if hits:
        flags.append("Instruction-like text aimed at an AI: " + "; ".join(f'"{h}"' for h in hits[:2]))
        exclude = True
    hidden = hidden_char_count(text)
    if hidden > 3:
        flags.append(f"{hidden} invisible characters that can hide instructions")
        exclude = True
    pii = find_pii(text)
    if pii:
        flags.append("Personal data: " + ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in sorted(pii.items())))
    return flags, exclude
