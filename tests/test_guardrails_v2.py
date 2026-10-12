from edrak.guardrails.chat import check_answer
from edrak.guardrails.output import check_brief

METRICS = {"u1": {"uid": "u1", "value": 19.0, "unit": "USD", "metric_id": "price_per_tier", "entity": "Fawry"}}


def _check(answer, label="brief", evs=(1,), uids=("u1",), web=""):
    return check_answer(answer, label, evidence_nos=list(evs), metric_uids=list(uids), brief_evidence_nos={1, 2}, brief_metrics=METRICS,
                        evidence_text={1: "Team plan is $19 per user per month", 2: "Founded in 2008"}, web_text=web)


def test_chat_keeps_stored_numbers_and_citations_and_removes_unknown_ones():
    r = _check("Fawry charges $19 per seat [E1]. Competitors charge $42.")
    assert "$19" in r.answer and "[E1]" in r.answer
    assert "$42" not in r.answer and r.removed_numbers == ["$42"]


def test_chat_without_any_brief_source_is_not_covered_and_foreign_citations_are_dropped():
    r = _check("They probably do well.", label="brief", evs=(99,), uids=())
    assert r.label == "not_covered" and r.evidence_nos == []


def test_web_answers_are_labelled_unverified_and_may_use_numbers_from_the_web_text():
    r = _check("Their revenue was $5M last year.", label="web_unverified", evs=(), uids=(), web="Revenue reached $5M in 2025")
    assert r.label == "web_unverified" and "$5M" in r.answer and "unverified" in r.answer.lower()
    r2 = _check("Their revenue was $9M.", label="web_unverified", evs=(), uids=(), web="Revenue reached $5M")
    assert "$9M" not in r2.answer


def test_chat_softens_recommendation_wording():
    r = _check("The best option is to match price. Fawry is $19 [E1].")
    assert "best option" not in r.answer.lower()


def test_brief_check_blocks_invented_numbers_in_card_text_and_unsourced_cards():
    doc = {"cards": {"k1": {"card_key": "k1", "component": "kpi", "title": "Fawry price", "headline": "Fawry costs $99", "refs": [1], "metric_uids": ["u1"], "status": "verified"},
                     "k2": {"card_key": "k2", "component": "kpi", "title": "Orphan", "headline": "Fawry costs $19", "refs": [], "metric_uids": [], "status": "verified"}},
           "metrics": {"u1": {"value": 19.0}}, "headline": "Fawry leads at $19", "evidence": [], "lenses": [], "options": [
               {"key": "A", "title": "Hold", "thesis": "We recommend holding", "supporting_signal_ids": []}],
           "signals_view": [{"signal_id": "s1", "why_it_matters": "Margins fall 40%"}], "question": "q"}
    out = check_brief(doc)
    assert out["cards"]["k1"]["headline"] == "Fawry price"       # $99 is not a stored value
    assert out["cards"]["k2"]["headline"] == "Fawry costs $19"   # a stored value is fine
    assert out["cards"]["k2"]["status"] == "thin" and out["cards"]["k2"]["caveat"]
    assert out["signals_view"][0]["why_it_matters"] is None
    assert out["headline"] == "Fawry leads at $19"
    kinds = {c["check"] for c in out["guardrails"]["checks"]}
    assert {"card_number_not_in_table", "uncited_card", "uncited_option", "why_it_matters_dropped"} <= kinds
