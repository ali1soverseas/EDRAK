"""A scripted model and sample sources for the company fact extraction tests."""

from __future__ import annotations

import asyncio
import json

from edrak.company_research.extraction import ExtractionLimits, SourceDoc, extract_company_facts
from edrak.company_research.models import CompanyFact, SourceTier

WS = "ws-1"

EN_PAGE = (
    "Acme Pay was founded in 2009 by Amal Nasr. Our headquarters are in Cairo, Egypt. "
    "Amal Nasr is the Chief Executive Officer of Acme Pay."
)
AR_PAGE = "يشغل السيد عمرو حسن منصب المدير المالي للشركة منذ عام ٢٠١٥. المقر الرئيسي للشركة في القاهرة."
OCR_PAGE = "Annua1 Revenue for 2O23 reached EGP 1.2 bi1lion, up 18 percent over the previous year."


def fact(source="S1", quote="Amal Nasr is the Chief Executive Officer of Acme Pay", **overrides) -> dict:
    values = dict(
        category="leadership", key="ceo", label="Chief Executive Officer", value="Amal Nasr", value_type="text",
        source=source, quote=quote, confidence="high",
    )
    values.update(overrides)
    return values


def reply(*facts: dict) -> str:
    return json.dumps({"facts": list(facts)}, ensure_ascii=False)


class Llm:
    """A model that answers each call with the next scripted reply. A reply that is an exception is raised.

    The last reply repeats when the script runs out. Every call's messages are kept in `calls`.
    """

    def __init__(self, *replies) -> None:
        self.replies = list(replies) or [reply()]
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        item = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(item, Exception):
            raise item
        return item(messages) if callable(item) else item

    @property
    def sources_text(self) -> str:
        return "\n".join(c[1]["content"] for c in self.calls)


def doc(text=EN_PAGE, url="https://acme.example/about", tier=SourceTier.OWN_SITE, **kw) -> SourceDoc:
    return SourceDoc(url=url, text=text, tier=tier, **kw)


def arun(documents, llm, *, existing=(), limits=None, workspace_id=WS, run_id="run-1", **kwargs):
    """The extraction as a coroutine, for tests that are already async."""
    limits = limits or ExtractionLimits(batch_chars=24000, max_batches=30, ocr_tolerance=0.9)
    return extract_company_facts(
        documents, workspace_id=workspace_id, run_id=run_id, llm=llm, existing=existing, limits=limits, **kwargs
    )


def run(documents, llm, **kwargs):
    return asyncio.run(arun(documents, llm, **kwargs))


def stored(**overrides) -> CompanyFact:
    """A stored fact as the repository returns it."""
    values = dict(
        id="f1", workspace_id=WS, category="leadership", key="ceo", value="Amal Nasr", value_type="text",
        source_tier="own_site", source_url="https://acme.example/about", quote="Amal Nasr, CEO",
        observed_at="2026-01-01T00:00:00.000Z", confidence="high", status="suggested",
        created_at="2026-01-01T00:00:00.000Z", updated_at="2026-01-01T00:00:00.000Z",
    )
    values.update(overrides)
    return CompanyFact.model_validate(values)
