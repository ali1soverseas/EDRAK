"""Arabic for a brief is stored in the background. A read only uses that cache."""

from __future__ import annotations

import asyncio
import hashlib
import re

from edrak.db.connection import get_db
from edrak.exports.brief import to_markdown
from edrak.i18n.localize import (
    _mark_rtl,
    _protect_names,
    fill_arabic,
    localize_generated,
    schedule_brief_translation,
    search_sources,
)
from edrak.i18n.phrases import annotate_run, classify_alert, classify_warning


def _run(research_db_path, story):
    async def go():
        async with get_db() as db:
            return await story(db)

    return asyncio.run(go())


def test_english_request_does_not_call_the_translator(research_db_path, monkeypatch):
    calls: list[list[str]] = []

    def translate(texts: list[str]) -> list[str]:
        calls.append(texts)
        return texts

    monkeypatch.setattr("edrak.i18n.localize._default_translator", translate)
    payload = {"title": "Market is opening", "summary": "Demand is growing in the region."}

    out = _run(research_db_path, lambda db: localize_generated(db, payload, "en"))
    arabic = _run(research_db_path, lambda db: localize_generated(db, payload, "ar"))

    assert out == payload
    assert arabic == payload
    assert calls == []


def test_arabic_translates_prose_once_and_skips_the_rest(research_db_path):
    calls: list[list[str]] = []

    def translate(texts: list[str]) -> list[str]:
        calls.append(list(texts))
        return [f"ع:{text}" for text in texts]

    alert = {
        "title": "Competitor cut prices",
        "statement": "Competitor cut prices",
        "why_it_matters": "A competitor intelligence finding added since the last run.",
        "what_changed": "Before: Competitor cut prices",
    }
    classify_alert(alert)
    view = annotate_run({
        "events": [
            {"message": "The brief is ready.", "kind": "status"},
            {"message": "No evidence found. No matching evidence was found.", "kind": "gap"},
            {"message": "No evidence found. Prices were not published.", "kind": "gap"},
            {"message": "Workers should look at pricing again.", "kind": "replan"},
        ],
        "tasks": [],
    })
    payload = {
        "title": "Market is opening",
        "summary": "Demand is growing in the region.",
        "excerpt": "Leave this quote in English.",
        "codes": [{"title": "abcdef0123456789abcdef"}],
        "messages": [
            {"role": "user", "content": "What should we do next?"},
            {"role": "assistant", "content": "The market is opening."},
        ],
        "decision": {
            "decision_id": "d1",
            "recorded_at": "2026-01-01T00:00:00Z",
            "option_label": "Wait",
            "rationale": "I chose this myself.",
        },
        "source": {"text": "A quoted passage.", "excerpt": "A quoted passage.", "source_url": "https://example.com/a"},
        "claim": {"statement": "A verified claim about pricing."},
        "alert": alert,
        "run": view,
    }

    async def once(db):
        await fill_arabic(db, payload, translate=translate)
        return await localize_generated(db, payload, "ar")

    first = _run(research_db_path, once)
    second = _run(research_db_path, once)

    seen = [text for batch in calls for text in batch]
    assert seen.count("Market is opening") == 1
    assert seen.count("Competitor cut prices") == 1
    assert "The brief is ready." not in seen
    assert "No evidence found. No matching evidence was found." not in seen
    assert "No evidence found. Prices were not published." not in seen
    assert "Prices were not published." in seen
    assert "Workers should look at pricing again." in seen
    assert "What should we do next?" not in seen
    assert "I chose this myself." not in seen
    assert "A quoted passage." not in seen
    assert "Leave this quote in English." not in seen
    assert "abcdef0123456789abcdef" not in seen
    assert "A competitor intelligence finding added since the last run." not in seen

    assert first["title"] == _mark_rtl("ع:Market is opening")
    assert first["messages"][0]["content"] == "What should we do next?"
    assert first["messages"][1]["content"] == _mark_rtl("ع: ال market is opening.")
    assert first["decision"]["rationale"] == "I chose this myself."
    assert first["claim"]["statement"] == _mark_rtl("ع:A verified claim about pricing.")
    assert first["alert"]["title"] == _mark_rtl("ع:Competitor cut prices")
    assert first["alert"]["why_key"] == "alert.sinceLastRun"
    assert first["alert"]["what_key"] == "alert.before"
    assert first["alert"]["why_it_matters"].startswith("A competitor")
    assert first["run"]["events"][0]["message_key"] == "run.briefReady"
    assert first["run"]["events"][0]["message"] == "The brief is ready."
    assert first["run"]["events"][1]["message_key"] == "run.noEvidenceNone"
    assert first["run"]["events"][2]["message_params"]["reason"] == _mark_rtl("ع:Prices were not published.")
    assert first["run"]["events"][3]["generated"] is True
    assert first["run"]["events"][3]["message"] == _mark_rtl("ع:Workers should look at pricing again.")
    assert second == first
    assert len(calls) == 1

    async def rows(db):
        cursor = await db.execute("SELECT count(*) FROM text_translations")
        return (await cursor.fetchone())[0]

    assert _run(research_db_path, rows) == len(set(seen))


def test_a_changed_sentence_is_a_new_cache_row(research_db_path):
    calls: list[str] = []

    def translate(texts: list[str]) -> list[str]:
        calls.extend(texts)
        return [f"ع:{text}" for text in texts]

    async def story(db):
        await fill_arabic(db, {"title": "Market is opening"}, translate=translate)
        await fill_arabic(db, {"title": "Market is opening now"}, translate=translate)
        return await localize_generated(db, {"title": "Market is opening now"}, "ar")

    out = _run(research_db_path, story)

    assert calls == ["Market is opening", "Market is opening now"]
    assert out["title"] == _mark_rtl("ع:Market is opening now")

    async def rows(db):
        cursor = await db.execute("SELECT translated_text FROM text_translations")
        return [row[0] for row in await cursor.fetchall()]

    stored = _run(research_db_path, rows)
    assert stored == ["ع:Market is opening", "ع:Market is opening now"]
    assert all(not text.startswith("\u061c") for text in stored)


def test_a_blank_translation_keeps_the_english(research_db_path):
    def translate(texts: list[str]) -> list[str]:
        return ["" for _ in texts]

    async def story(db):
        await fill_arabic(db, {"title": "Market is opening"}, translate=translate)
        return await localize_generated(db, {"title": "Market is opening"}, "ar")

    out = _run(research_db_path, story)

    assert out["title"] == "Market is opening"

    async def rows(db):
        cursor = await db.execute("SELECT count(*) FROM text_translations")
        return (await cursor.fetchone())[0]

    assert _run(research_db_path, rows) == 0


def test_fixed_warning_and_search_use_the_dictionary_and_the_cache(research_db_path):
    warning = classify_warning("2 link(s) were ignored because they were not valid.")
    assert warning["key"] == "import.linksIgnored"
    assert warning["params"] == {"n": "2"}

    def translate(texts: list[str]) -> list[str]:
        return [f"ع:{text}" for text in texts]

    _run(research_db_path, lambda db: fill_arabic(db, {"title": "Market is opening"}, translate=translate))

    async def found(db):
        return await search_sources(db, "%ع:Market%")

    assert _run(research_db_path, found) == ["Market is opening"]


def test_brief_fields_and_heavy_trees(research_db_path):
    calls: list[str] = []

    def translate(texts: list[str]) -> list[str]:
        calls.extend(texts)
        return [f"ع:{text}" for text in texts]

    payload = {
        "analysis_title": "Entry into Riyadh",
        "title": "Market is opening",
        "why_it_matters": "Pricing pressure is rising.",
        "trade_offs": ["Less coverage", "More cost"],
        "what_we_would_do": ["Pilot in one city"],
        "cost_of_waiting": "Share erodes.",
        "definition": "List price for the mid tier.",
        "subtitle": "Our figures next to each competitor.",
        "framing": "Frame each option as a response.",
        "collapsed_reason": "No figure could be found and checked for this question.",
        "collapsed_reason_key": "brief.metricsEmpty",
        "orchestration": {"results": [{"findings": [{"statement": "Buried worker prose."}]}]},
        "request": {"company_profile": {"name": "NVIDIA", "competitors": ["Fawry"]}},
        "signal": "NVIDIA and Fawry both cut mid-tier prices.",
    }

    async def story(db):
        await fill_arabic(db, payload, translate=translate)
        return await localize_generated(db, payload, "ar")

    out = _run(research_db_path, story)

    assert out["analysis_title"] == _mark_rtl("ع:Entry into Riyadh")
    assert out["why_it_matters"] == _mark_rtl("ع:Pricing pressure is rising.")
    assert out["trade_offs"] == [_mark_rtl("ع:Less coverage"), _mark_rtl("ع:More cost")]
    assert out["what_we_would_do"] == [_mark_rtl("ع:Pilot in one city")]
    assert out["cost_of_waiting"] == _mark_rtl("ع:Share erodes.")
    assert out["definition"] == _mark_rtl("ع:List price for ال mid tier.")
    assert out["subtitle"].startswith(_mark_rtl("ع:"))
    assert out["framing"].startswith(_mark_rtl("ع:"))
    assert out["collapsed_reason"] == "No figure could be found and checked for this question."
    assert "Buried worker prose." not in calls
    assert "NVIDIA" in out["signal"]
    assert "Fawry" in out["signal"]
    assert out["signal"].startswith(_mark_rtl("ع:"))


def test_a_saved_brief_translates_after_the_caller_returns(research_db_path):
    calls: list[list[str]] = []

    def translate(texts: list[str]) -> list[str]:
        calls.append(list(texts))
        return [f"ع:{text}" for text in texts]

    async def story(db):
        task = schedule_brief_translation({"title": "Market is opening"}, translate=translate)
        assert calls == []
        await task
        return await localize_generated(db, {"title": "Market is opening"}, "ar")

    out = _run(research_db_path, story)

    assert calls == [["Market is opening"]]
    assert out["title"] == _mark_rtl("ع:Market is opening")


def test_card_fields_are_translated_and_buried_verification_text_is_not(research_db_path):
    calls: list[str] = []

    def translate(texts: list[str]) -> list[str]:
        calls.extend(texts)
        return [f"ع:{text}" for text in texts]

    payload = {
        "slots": {"rows": [{"label": "H100 list price"}]},
        "options": [{"guardrail": "Stay inside the residency rules."}],
        "verification": {
            "control_summary": {
                "missing_information": ["No GPU supply figure."],
                "conflicts": ["Two prices for the same GPU."],
            },
            "findings": [{"statement": "Do not translate this buried note."}],
        },
    }

    async def story(db):
        await fill_arabic(db, payload, translate=translate)
        return await localize_generated(db, payload, "ar")

    out = _run(research_db_path, story)

    assert out["slots"]["rows"][0]["label"].startswith(_mark_rtl("ع:"))
    assert "H100" in out["slots"]["rows"][0]["label"]
    assert out["options"][0]["guardrail"].startswith(_mark_rtl("ع:"))
    assert out["verification"]["control_summary"]["missing_information"][0].startswith(_mark_rtl("ع:"))
    assert "GPU" in out["verification"]["control_summary"]["missing_information"][0]
    assert "GPU" in out["verification"]["control_summary"]["conflicts"][0]
    assert "Do not translate this buried note." not in calls
    assert out["verification"]["findings"][0]["statement"] == "Do not translate this buried note."


def test_articles_are_not_left_in_english(research_db_path):
    def translate(texts: list[str]) -> list[str]:
        return [f"ع:{text}" for text in texts]

    source = "The market is opening. A competitor cut a price."

    async def story(db):
        await fill_arabic(db, {"title": source}, translate=translate)
        return await localize_generated(db, {"title": source}, "ar")

    out = _run(research_db_path, story)["title"]

    assert "ال" in out
    assert not re.search(r"\b(?:the|an|a)\b", out)
    assert "A" in out
    assert "ألف" not in out


def test_gpu_stays_untranslated_and_a_bad_cache_row_is_not_reused(research_db_path, monkeypatch):
    calls: list[str] = []

    def translate(texts: list[str]) -> list[str]:
        calls.extend(texts)
        return [f"ع:{text}" for text in texts]

    monkeypatch.setattr("edrak.i18n.localize._default_translator", translate)
    source = "Buy a GPU this quarter."

    async def story(db):
        await db.execute(
            "INSERT INTO text_translations (source_hash, target_lang, source_text, translated_text) VALUES (?, 'ar', ?, ?)",
            (hashlib.sha256(source.encode()).hexdigest(), source, "اشتر هذا الربع."),
        )
        await db.commit()
        before = await localize_generated(db, {"signal": source}, "ar")
        await fill_arabic(db, {"signal": source}, translate=translate)
        after = await localize_generated(db, {"signal": source}, "ar")
        return before, after

    before, after = _run(research_db_path, story)

    assert before["signal"] != "اشتر هذا الربع."
    assert "GPU" not in "".join(calls)
    assert before["signal"].startswith(_mark_rtl("ع:"))
    assert "GPU" in before["signal"]
    assert after == before


def test_a_word_that_starts_with_a_capital_stays_english(research_db_path):
    calls: list[str] = []

    def translate(texts: list[str]) -> list[str]:
        calls.extend(texts)
        return [f"ع:{text}" for text in texts]

    source = "Demand from Intel and Blackwell stayed firm."

    async def story(db):
        await fill_arabic(db, {"signal": source}, translate=translate)
        return await localize_generated(db, {"signal": source}, "ar")

    out = _run(research_db_path, story)["signal"]

    sent = "".join(calls)
    assert "Intel" not in sent
    assert "Blackwell" not in sent
    assert "Demand" in sent
    assert out.startswith(_mark_rtl("ع:"))
    assert "Intel" in out and "Blackwell" in out


def test_a_sentence_opener_is_still_sent_to_the_translator():
    protected, _mapping = _protect_names("Prices rose. Then Intel cut supply.", [])
    assert protected.startswith("Prices rose. Then ")
    assert "Then" in protected
    assert "Intel" not in protected


def test_export_heading_follows_the_page_language():
    doc = {"title": "Market is opening", "brief_no": 4, "summary": {"found": [{"t": "text", "text": "Demand is growing."}]}}
    english = to_markdown(doc).decode()
    arabic = to_markdown(doc, lang="ar").decode()
    assert "## Summary" in english
    assert "Evidence status:" in english
    assert "## الملخص" in arabic
    assert "حالة الأدلة:" in arabic
    assert "الموجز 0004:" in arabic
