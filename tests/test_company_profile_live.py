"""Live smoke test for profile extraction. Skipped unless OLLAMA_API_KEY is configured.

Makes one real call to the configured model (OLLAMA_MODEL, gpt-oss:120b by default).
Run: python -m pytest tests/test_company_profile_live.py -s
"""

import pytest

from edrak.company_profile import SourceDocument, SourceType, build_profile_draft
from edrak.company_profile.validate import ALLOWED_INDUSTRIES
from edrak.core.llm import provider_key

pytestmark = pytest.mark.skipif(
    not provider_key("OLLAMA_API_KEY")[0], reason="OLLAMA_API_KEY is not configured."
)

HOME = """Nile Ledger | نايل ليدجر
Nile Ledger builds bookkeeping software for small shops and restaurants in Egypt and Jordan.
Our products: Ledger Lite, Ledger POS and Ledger Payroll.
Our mission: to give every small business a clear view of its money.
"""

ABOUT = """About Nile Ledger
Founded in Cairo, Nile Ledger is a software-as-a-service company. We serve merchants in Egypt and Jordan,
and we plan to open in Saudi Arabia by 2027. Contact: hello@nileledger.example
"""


def test_live_extraction_returns_a_cited_draft():
    draft = build_profile_draft(
        [
            SourceDocument(source_type=SourceType.WEB_PAGE, ref="https://nileledger.example/", title="Home", text=HOME, priority=0),
            SourceDocument(source_type=SourceType.WEB_PAGE, ref="https://nileledger.example/about", title="About", text=ABOUT, priority=1),
        ],
        extra_links=[{"platform": "website", "url": "https://nileledger.example"}],
    )
    print(draft.model_dump_json(indent=2))

    assert draft.profile.name and "nile ledger" in draft.profile.name.lower()
    assert draft.profile.industry in ALLOWED_INDUSTRIES
    assert draft.profile.products
    sent = {s.id for s in draft.sources}
    assert sent == {"S1", "S2"}
    for field_name, ids in draft.field_sources.items():
        assert ids and set(ids) <= sent
        assert field_name not in draft.gaps
