import json

import pytest

from edrak.company_profile import extractor
from edrak.company_profile.draft import build_profile_draft
from edrak.company_profile.extractor import (
    ProfileExtractionError,
    ProfileLLMError,
    extract_profile,
)
from edrak.company_profile.models import SourceDocument, SourceType

VALID = {
    "profile": {
        "name": "Acme Pay",
        "aliases": ["أكمي باي"],
        "notes": "Acme Pay provides card payments to merchants.",
        "products": ["Acme Terminal", "Acme Link"],
        "industry": "Fintech",
        "markets": ["Egypt"],
        "goals": [],
    },
    "field_sources": {
        "name": ["S1"],
        "aliases": ["S1"],
        "notes": ["S1"],
        "products": ["S1", "S2"],
        "industry": ["S1"],
        "markets": ["S2"],
    },
}


class FakeLLM:
    """Returns the queued replies in order and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def page(ref, text, **kw):
    return SourceDocument(source_type=SourceType.WEB_PAGE, ref=ref, text=text, **kw)


def test_a_valid_reply_is_parsed_on_the_first_call():
    llm = FakeLLM(VALID)
    result = extract_profile("[S1] context", llm)
    assert result.profile.name == "Acme Pay"
    assert result.field_sources["markets"] == ["S2"]
    assert len(llm.calls) == 1
    system, user, schema = llm.calls[0]
    assert system["role"] == "system" and "Never guess" in system["content"]
    assert "fintech" in system["content"] and "other" in system["content"]
    assert user["content"].endswith("[S1] context")
    assert "JSON schema" in schema["content"] and "field_sources" in schema["content"]


def test_a_fenced_reply_is_accepted():
    result = extract_profile("ctx", FakeLLM("```json\n" + json.dumps(VALID) + "\n```"))
    assert result.profile.name == "Acme Pay"


def test_an_invalid_reply_is_repaired_once_with_the_error_in_the_prompt():
    bad = {"profile": {"name": "Acme Pay", "products": "Acme Terminal"}, "field_sources": {}}
    llm = FakeLLM(bad, VALID)
    result = extract_profile("ctx", llm)

    assert result.profile.products == ["Acme Terminal", "Acme Link"]
    assert len(llm.calls) == 2
    repair = llm.calls[1]
    assert repair[-2]["role"] == "assistant" and "Acme Terminal" in repair[-2]["content"]
    assert repair[-1]["role"] == "user"
    assert "profile.products" in repair[-1]["content"]  # the validation error names the field
    assert "Input should be a valid array" in repair[-1]["content"]


def test_two_invalid_replies_raise_the_typed_error():
    llm = FakeLLM("not json at all", {"profile": {"aliases": 5}})
    with pytest.raises(ProfileExtractionError) as caught:
        extract_profile("ctx", llm)
    assert caught.value.attempts == 2
    assert "aliases" in caught.value.last_error
    assert len(llm.calls) == 2  # no third attempt


def test_a_failing_model_call_is_a_typed_error_and_is_not_retried():
    llm = FakeLLM(RuntimeError("OLLAMA_API_KEY is not configured."))
    with pytest.raises(ProfileLLMError, match="OLLAMA_API_KEY"):
        extract_profile("ctx", llm)
    assert len(llm.calls) == 1


def test_the_default_call_uses_the_shared_client_with_schema_and_reasoning(monkeypatch):
    seen = {}

    class Model:
        def invoke(self, messages, **kwargs):
            seen.update(kwargs, messages=messages)
            return type("Reply", (), {"content": json.dumps(VALID)})()

    def get_chat_model(**kwargs):
        seen["client"] = kwargs
        return Model()

    monkeypatch.setattr(extractor, "get_chat_model", get_chat_model)
    monkeypatch.setattr(extractor.settings, "PROFILE_IMPORT_REASONING", "low")
    assert extract_profile("ctx").profile.name == "Acme Pay"
    assert seen["reasoning"] == "low"
    assert seen["client"] == {"temperature": 0}
    assert seen["format"]["type"] == "object" and "profile" in seen["format"]["properties"]

    monkeypatch.setattr(extractor.settings, "PROFILE_IMPORT_REASONING", "")
    seen.clear()
    extract_profile("ctx")
    assert "reasoning" not in seen


def test_the_draft_carries_validated_fields_sources_gaps_and_links():
    reply = json.loads(json.dumps(VALID))
    reply["profile"]["industry"] = "Space tourism"
    reply["field_sources"]["markets"] = ["S9"]  # a source that was never sent
    llm = FakeLLM(reply)
    draft = build_profile_draft(
        [page("https://acme.test/about", "About Acme Pay. " * 40, priority=1, title="About"), page("https://acme.test/", "Home. " * 80)],
        extra_links=[
            {"platform": "website", "url": "acme.test"},
            {"platform": "website", "url": "https://other.test"},
            {"platform": "x", "url": "https://x.com/acme"},
            {"platform": "x", "url": "https://X.com/acme"},
            {"platform": "myspace", "url": "https://myspace.com/acme"},
        ],
        llm=llm,
    )

    assert [(s.id, s.ref) for s in draft.sources] == [("S1", "https://acme.test/"), ("S2", "https://acme.test/about")]
    assert draft.profile.name == "Acme Pay"
    assert draft.profile.industry == "other"
    assert draft.profile.markets == []
    assert draft.field_sources["products"] == ["S1", "S2"]
    assert draft.gaps == ["markets", "goals"]
    assert [(l.platform.value, l.url) for l in draft.links] == [
        ("website", "https://acme.test"),
        ("x", "https://x.com/acme"),
    ]
    assert any("link(s) were ignored" in w for w in draft.warnings)
    assert any("markets" in w for w in draft.warnings)
    assert not any("incomplete" in w for w in draft.warnings)


def test_thin_text_is_flagged():
    draft = build_profile_draft([page("https://acme.test/", "Acme Pay.")], llm=FakeLLM(VALID))
    assert any("likely incomplete" in w for w in draft.warnings)


def test_no_usable_text_means_no_model_call():
    llm = FakeLLM()
    draft = build_profile_draft([page("https://acme.test/", "   ")], extra_links=[{"platform": "website", "url": "acme.test"}], llm=llm)
    assert llm.calls == []
    assert draft.profile.name is None and draft.sources == []
    assert draft.gaps[0] == "name" and len(draft.gaps) == 7
    assert [l.url for l in draft.links] == ["https://acme.test"]
    assert any("no readable text" in w.lower() for w in draft.warnings)
