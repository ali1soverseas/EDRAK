"""Every criteria pair produces an emphasis sentence for the on-screen note.

These cases do not call a model. The sentence is not written into either prompt.
Option generation is steered only by stored tag summaries.
"""

from edrak.DecisionAnalysis.nodes import build_decision_analysis_user_prompt
from edrak.DecisionAnalysis.options import _prompt
from edrak.DecisionAnalysis.pair_cases import KINDS, build_cases, pairs
from edrak.DecisionAnalysis.personalization import CRITERIA, RELATION_BY_ID, RELATIONS


def _context(emphasis: str) -> dict:
    return {
        "business_goal": "determine if entering the UAE AI compute market is advisable",
        "business_request": {
            "company_profile": {"name": "NVIDIA"},
            "business_context": {"use_case": "competitive_intelligence"},
        },
        "decision_questions": ["How should NVIDIA time its entry?"],
        "signals": [],
        "cross_signal": {},
        "analysis_emphasis": emphasis,
    }


def test_every_defined_pair_has_three_cases():
    cases = build_cases()
    assert len(pairs()) == len(RELATIONS)
    assert len(cases) == len(RELATIONS) * len(KINDS)
    assert len({case["id"] for case in cases}) == len(cases)


def test_shared_history_keeps_the_narrower_note_even_when_one_criterion_outweighs():
    by_kind = {}
    for case in build_cases():
        by_kind.setdefault(case["relation"], {})[case["kind"]] = case
    for relation, kinds in by_kind.items():
        spec = RELATION_BY_ID[relation]
        together = kinds["together"]
        outweighed = kinds["outweighed"]
        assert together["label"] == spec["label"]
        assert together["note"] == f"This analysis looks more closely at {spec['label']}, based on earlier decisions: {together['pair'][0]} and {together['pair'][1]}, {together['pair'][0]} and {together['pair'][1]} again."
        assert together["emphasis"] == together["note"]
        assert together["preference"]["status"] == "applied"
        assert together["preference"]["relation"] == relation
        assert outweighed["preference"]["status"] == "applied"
        assert outweighed["preference"]["relation"] == relation
        assert outweighed["note"].startswith(f"This analysis looks more closely at {spec['label']}")
        assert set(together["pair"]).issubset(together["preference"]["aspects"])


def test_a_split_asks_for_the_trade_off_between_the_two_focuses():
    for case in build_cases():
        if case["kind"] != "split":
            continue
        left, right = case["pair"]
        assert case["preference"]["status"] == "applied"
        assert case["preference"]["criteria"] == [left, right]
        assert case["note"] == (
            f"This analysis looks more closely at {CRITERIA[left]['label']} and {CRITERIA[right]['label']}, "
            f"based on earlier decisions: {left} only, {left} only again, {right} only, {right} only again."
        )


def test_emphasis_sentences_stay_out_of_both_prompts():
    for case in build_cases():
        context = _context(case["emphasis"])
        assert case["emphasis"] not in build_decision_analysis_user_prompt(context)
        assert case["emphasis"] not in _prompt(context)
        assert "ANALYSIS EMPHASIS" not in _prompt(context)
        assert "CRITERIA FOCUS" not in _prompt(context)
        assert "past decisions" not in case["emphasis"].lower()
