"""Every criteria pair produces the emphasis the decision agent should be given.

These cases do not call a model. The runner in scripts/run_decision_pair_cases.py
sends the same saved cross-signal through the decision agent once per case.
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


def test_shared_history_keeps_the_narrower_sentence_even_when_one_criterion_outweighs():
    by_kind = {}
    for case in build_cases():
        by_kind.setdefault(case["relation"], {})[case["kind"]] = case
    for relation, kinds in by_kind.items():
        spec = RELATION_BY_ID[relation]
        together = kinds["together"]
        outweighed = kinds["outweighed"]
        sentence = f"Examine {spec['focus']} in more depth."
        assert together["label"] == spec["label"]
        assert together["emphasis"] == sentence
        assert together["preference"]["status"] == "applied"
        assert together["preference"]["relation"] == relation
        assert outweighed["preference"]["status"] == "applied"
        assert outweighed["preference"]["relation"] == relation
        assert outweighed["emphasis"] == sentence
        assert set(together["pair"]).issubset(together["preference"]["aspects"])


def test_a_split_asks_for_the_trade_off_between_the_two_focuses():
    for case in build_cases():
        if case["kind"] != "split":
            continue
        left, right = case["pair"]
        assert case["preference"]["status"] == "uncertain"
        assert case["preference"]["criteria"] == [left, right]
        assert case["emphasis"] == (
            f"Make the trade-off between {CRITERIA[left]['focus']} and {CRITERIA[right]['focus']} explicit."
        )


def test_each_emphasis_is_what_both_decision_prompts_receive():
    for case in build_cases():
        context = _context(case["emphasis"])
        assert case["emphasis"] in build_decision_analysis_user_prompt(context)
        assert case["emphasis"] in _prompt(context)
        assert "past decisions" not in case["emphasis"].lower()
