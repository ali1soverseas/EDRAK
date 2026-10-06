"""Arabic and English mixed in one text, Arabic-Indic digits, right to left display."""

import json
from typing import Any

import pytest

from edrak.agents.customer_trends.llm.fake import ScriptedChatModel
from edrak.agents.customer_trends.schemas.common import Platform
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools import analyze_text, compute_metrics, submit_findings
from edrak.agents.customer_trends.tools.base import ToolContext, invoke_tool
from edrak.agents.customer_trends.ui.components.format import is_rtl, rtl_html
from edrak.agents.customer_trends.utils.text import content_hash, detect_language
from tests.customer_trends.factories import RUN_ID, TASK_ID, make_evidence
from tests.customer_trends.tool_helpers import processing_context

MIXED = "الدعم الفني بطيء جدا and the replies take ٣ أيام to arrive 👍 @gitlab #devops"
DIACRITICS = "اعتمدنا جِيت لاب في الشركة وهو ممتاز"


def store_items(
    store: EvidenceStore, *texts: str, languages: tuple[str | None, ...] = ()
) -> list[str]:
    store.create_run(RUN_ID, TASK_ID)
    given = [*languages, *([None] * len(texts))][: len(texts)]
    items = [
        make_evidence(t, platform=Platform.X, language=lang)
        for t, lang in zip(texts, given, strict=True)
    ]
    store.add_batch(RUN_ID, TASK_ID, "test", items)
    return [i.id for i in items]


def test_mixed_text_is_labelled_with_its_main_language() -> None:
    assert detect_language("GitLab Duo رائع جدا للمطورين العرب في المنطقة كلها") == "ar"
    assert detect_language("The pricing keeps going up every year but الدعم ممتاز") == "en"
    assert detect_language("Duo رائع") == "ar"
    assert detect_language("😀👍") is None


def test_the_same_comment_with_other_spelling_marks_is_a_duplicate() -> None:
    assert content_hash("جِيت لاب   ممتاز") == content_hash("جيت لاب ممتاز")
    assert content_hash("ممتــــاز") == content_hash("ممتاز")
    assert content_hash("Great TOOL") == content_hash("great  tool")


def test_text_survives_the_store_exactly_as_written(store: EvidenceStore) -> None:
    [item_id] = store_items(store, MIXED, DIACRITICS)[:1]
    [stored] = store.get_items(RUN_ID, [item_id])
    assert stored.text == MIXED
    again = store.query(RUN_ID, limit=10).items
    assert {i.text for i in again} == {MIXED, DIACRITICS}


async def test_a_quote_from_mixed_text_is_verbatim(store: EvidenceStore) -> None:
    ids = store_items(store, MIXED, DIACRITICS, languages=("en", "ar"))
    model = ScriptedChatModel(
        responses=[
            {
                "items": [
                    {
                        "id": ids[0],
                        "sentiment": "negative",
                        "themes": ["slow support"],
                        "language": "ar",
                    },
                    {
                        "id": ids[1],
                        "sentiment": "positive",
                        "themes": ["slow support"],
                        "language": "ar",
                    },
                ],
                "candidate_themes": [],
            }
        ]
    )
    ctx = processing_context(store, analyst=model)
    batch_id = store.run_summary(RUN_ID).batch_ids[0]
    answer = await invoke_tool(ctx, analyze_text.SPEC, {"batch_ids": [batch_id]})
    assert answer.count == 2
    [theme] = store.get_aggregates(RUN_ID)
    assert set(theme.representative_quotes) == {MIXED, DIACRITICS}
    assert theme.by_language == {"ar": 1, "en": 1}, "the stored language of each item, not a guess"
    sent = json.dumps(model.calls[0][1].content, ensure_ascii=False)
    assert "الدعم الفني بطيء جدا" in sent, "Arabic goes to the model as written"


async def metrics(ctx: ToolContext, name: str, **params: Any) -> Any:
    return await invoke_tool(ctx, compute_metrics.SPEC, {"metric": name, "params": params})


async def test_a_brand_written_in_arabic_is_found_whatever_its_marks(store: EvidenceStore) -> None:
    store_items(store, DIACRITICS, "ننصح بـ جــيت لاب دائما", "لا علاقة لهذا النص")
    ctx = processing_context(store)
    answer = await metrics(ctx, "share_of_voice", entity="جيت لاب", competitors=["جيت هب"])
    assert answer.values["mentions"] == {"جيت لاب": 2, "جيت هب": 0}
    assert answer.values["items_without_a_mention"] == 1


async def test_a_finding_may_state_its_numbers_in_arabic_indic_digits(
    loaded_store: EvidenceStore,
) -> None:
    ctx = processing_context(loaded_store)
    ids = [i.id for i in loaded_store.query(RUN_ID, limit=3).items]

    def finding(fid: str, claim: str, metrics: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": fid,
            "type": "pain_point",
            "claim": claim,
            "confidence": "medium",
            "evidence_ids": ids[:2],
            "metrics": metrics,
        }

    answer = await invoke_tool(
        ctx,
        submit_findings.SPEC,
        {
            "findings": [
                finding("f1", "٣٤٫٥٪ من العناصر تذكر الدعم البطيء.", {"share_pct": 34.5}),
                finding("f2", "ظهرت الشكوى في ١٢٣٬٠٠٠ منشور.", {"posts": 123000}),
                finding("f3", "Support is slow in 34.5% of items.", {"share": "٣٤٫٥٪"}),
                finding("f4", "تذكر ٩٩٪ من العناصر الدعم.", {"share_pct": 34.5}),
            ]
        },
    )
    assert answer.accepted == ["f1", "f2", "f3"]
    [rejected] = answer.rejected
    assert rejected.id == "f4" and "not in metrics: 99" in rejected.reasons[0]


@pytest.mark.parametrize(
    ("text", "rtl"),
    [
        ("الدعم الفني بطيء", True),
        ("Support is slow", False),
        (MIXED, True),
        ("a long English sentence about GitLab with one كلمة inside it", False),
        ("جيت لاب هو أفضل خيار لفريقنا في هذا المجال و GitLab", True),
    ],
)
def test_the_right_to_left_helper_follows_the_main_script(text: str, rtl: bool) -> None:
    assert is_rtl(text) is rtl
    assert ('dir="rtl"' in rtl_html(text)) is rtl


def test_markup_in_arabic_text_is_escaped_in_the_rtl_block() -> None:
    block = rtl_html("الدعم <b>بطيء</b> & مكلف")
    assert "&lt;b&gt;" in block and "<b>" not in block and "&amp;" in block
    assert block.startswith('<div dir="rtl"')
