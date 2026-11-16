"""Decision tags and the emphasis they produce. No model calls."""

import json

from edrak.DecisionAnalysis.nodes import build_decision_analysis_user_prompt
from edrak.DecisionAnalysis.options import _prompt, problems, DraftOption, DraftOpportunity, DraftRisk
from edrak.DecisionAnalysis.personalization import CRITERIA, emphasis_text, infer_preference, note_text, tag_decision

NEW_ASPECTS = ("scale", "differentiation", "reversibility", "compliance", "revenue", "customer", "partnership", "talent")


def _option(key, title, thesis="", trade_offs=(), opportunities=()):
    return {"key": key, "title": title, "thesis": thesis, "trade_offs": list(trade_offs),
            "opportunities": [{"text": item} for item in opportunities]}


def _row(criterion, *, reason=False, title="Buy the tool"):
    tags = {"version": 1, "option": [] if reason else [criterion], "reason": [criterion] if reason else []}
    return {"id": f"{criterion}-{reason}-{title}", "title": title, "option_label": "A", "preference_tags": json.dumps(tags)}


def test_the_chosen_option_is_tagged_only_where_it_differs():
    options = [
        _option("A", "Launch fast", "Ship with a partner this quarter", opportunities=["Reach the market sooner"]),
        _option("B", "Build in-house", "Staff a team and keep ownership", opportunities=["Long-term control"]),
        _option("C", "Wait", "Close the evidence gap before committing"),
    ]
    tags = tag_decision(options, "A", None)
    assert tags["option"] == ["speed"] and tags["reason"] == []

    # A cost line in a trade-off is what the option gives up, not a preference.
    costly = _option("A", "Launch fast", trade_offs=["Higher upfront cost"])
    assert "cost" not in tag_decision([costly, options[1]], "A", None)["option"]

    # Every option mentions speed, so choosing one does not reveal a speed preference.
    shared = [_option(key, "Fast launch", "Ship quickly") for key in ("A", "B")]
    assert tag_decision(shared, "A", None)["option"] == []


def test_a_typed_reason_is_stronger_and_an_empty_reason_adds_nothing():
    options = [_option("A", "Hold position"), _option("B", "Match price")]
    assert tag_decision(options, "A", "We need this live before the deadline")["reason"] == ["speed"]
    assert tag_decision(options, "A", None)["reason"] == []
    assert tag_decision(options, "A", "   ")["reason"] == []


def test_inference_needs_two_decisions_and_names_a_split():
    assert infer_preference([])["status"] == "none"
    assert infer_preference([_row("speed")])["status"] == "none"
    applied = infer_preference([_row("speed", title="Portal"), _row("speed", title="Billing"), _row("cost", title="Once")])
    assert applied["status"] == "applied" and applied["criterion"] == "speed"
    assert [item["title"] for item in applied["decisions"]] == ["Portal", "Billing"]

    split = infer_preference([_row("speed"), _row("speed", title="Other"), _row("cost"), _row("cost", title="Again")])
    assert split["status"] == "uncertain" and split["criteria"] == ["speed", "cost"]

    # One stated reason outweighs one option tag, but still needs a second decision.
    weighted = infer_preference([_row("speed", reason=True), _row("cost"), _row("speed", title="Second")])
    assert weighted["status"] == "applied" and weighted["criterion"] == "speed"


def test_emphasis_reaches_both_prompts_only_when_history_applies():
    assert emphasis_text({"status": "none"}) is None
    assert emphasis_text(None) is None
    speed = emphasis_text({"status": "applied", "criterion": "speed"})
    assert "implementation time" in speed
    context = {"business_goal": "Choose a billing approach", "business_request": {}, "decision_questions": ["Build or buy?"],
               "signals": [], "cross_signal": {}}
    assert "analysis_emphasis" not in build_decision_analysis_user_prompt(context)
    assert "Examine" in build_decision_analysis_user_prompt({**context, "analysis_emphasis": speed})
    bare = {"business_goal": "Choose a billing approach", "business_request": {}, "signals": []}
    assert "ANALYSIS EMPHASIS" not in _prompt(bare)
    assert "ANALYSIS EMPHASIS" in _prompt({**bare, "analysis_emphasis": speed})
    assert note_text({"status": "applied", "criterion": "speed", "decisions": [{"title": "Portal"}]}) == (
        "This analysis looks more closely at implementation time, based on earlier decisions: Portal."
    )


def test_one_decision_keeps_every_aspect_it_matches():
    options = [
        _option("A", "Launch fast and cheap", "Ship sooner on a lower budget", opportunities=["A cheaper operating cost"]),
        _option("B", "Hold", "Keep the current offer"),
    ]
    tags = tag_decision(options, "A", "We need this before the deadline and it has to stay compliant")
    assert tags["option"] == ["speed", "cost"]
    assert tags["reason"] == ["speed", "compliance"]


def test_an_arabic_reason_is_translated_and_the_option_stays_english():
    seen = []

    def translate(text):
        seen.append(text)
        return "We need this before the deadline and it has to stay compliant"

    options = [_option("A", "Hold position"), _option("B", "Match price")]
    tags = tag_decision(options, "A", "نحتاج هذا قبل الموعد ويجب أن يبقى متوافقاً", translate=translate)
    assert seen == ["نحتاج هذا قبل الموعد ويجب أن يبقى متوافقاً"]
    assert tags["reason"] == ["speed", "compliance"]
    assert tag_decision(options, "A", "We need this live before the deadline", translate=lambda text: seen.append(text))["reason"] == ["speed"]
    assert seen == ["نحتاج هذا قبل الموعد ويجب أن يبقى متوافقاً"]


def _tagged(option, reason=(), title="Buy the tool"):
    tags = {"version": 2, "option": list(option), "reason": list(reason)}
    return {"id": title, "title": title, "option_label": "A", "preference_tags": json.dumps(tags), "recorded_at": title}


def test_the_same_decisions_narrow_the_focus_when_speed_outweighs_cost():
    rows = [
        _tagged(["speed", "cost"], ["speed"], "Portal"),
        _tagged(["speed", "cost"], ["speed"], "Billing"),
    ]
    applied = infer_preference(rows)
    assert applied["status"] == "applied" and applied["relation"] == "time_to_value"
    assert "speed" in applied["aspects"] and "cost" in applied["aspects"]
    sentence = emphasis_text(applied)
    assert "how quickly the spend is recovered" in sentence
    context = {"business_goal": "Choose a billing approach", "business_request": {}, "decision_questions": ["Build or buy?"],
               "signals": [], "cross_signal": {}}
    assert "Examine" in build_decision_analysis_user_prompt({**context, "analysis_emphasis": sentence})
    assert "ANALYSIS EMPHASIS" in _prompt({"business_goal": "Choose a billing approach", "business_request": {}, "signals": [], "analysis_emphasis": sentence})
    assert "time to value" in note_text(applied)


def test_a_split_across_decisions_stays_a_trade_off():
    split = infer_preference([_row("speed"), _row("speed", title="Other"), _row("cost"), _row("cost", title="Again")])
    assert split["status"] == "uncertain" and split["criteria"] == ["speed", "cost"]


def test_a_weaker_aspect_on_other_decisions_leaves_the_leader():
    applied = infer_preference([
        _row("speed"), _row("speed", title="Second"), _row("speed", title="Third"), _row("cost", title="Once"),
    ])
    assert applied["status"] == "applied" and applied["criterion"] == "speed"


def test_an_aspect_must_clear_the_bar_by_score_or_count():
    assert infer_preference([_row("speed")], aspect_bar=2)["status"] == "none"
    assert infer_preference([_row("speed", reason=True)], aspect_bar=2)["criterion"] == "speed"
    assert infer_preference([_row("speed"), _row("speed", title="Billing")], aspect_bar=2)["criterion"] == "speed"


def test_select_relevant_keeps_the_closest_and_stops_when_too_few_pass():
    from edrak.DecisionAnalysis.personalization import select_relevant

    vectors = {
        "uae": [1.0, 0.0],
        "close": [0.95, 0.05],
        "mid": [0.8, 0.6],
        "far": [0.0, 1.0],
    }

    def embed(texts):
        out = []
        for text in texts:
            key = next((name for name in vectors if name in text), "uae")
            out.append(vectors[key])
        return out

    request = {"goal": "uae market entry", "business_context": {"markets": ["uae"]}}
    rows = [
        {"id": "1", "title": "far billing", "question": "far", "recorded_at": "2026-03-01", "preference_tags": "{}"},
        {"id": "2", "title": "close entry", "question": "close", "recorded_at": "2026-01-01", "preference_tags": "{}"},
        {"id": "3", "title": "mid entry", "question": "mid", "recorded_at": "2026-02-01", "preference_tags": "{}"},
        {"id": "4", "title": "closer entry", "question": "close", "recorded_at": "2026-04-01", "preference_tags": "{}"},
    ]
    kept = select_relevant(rows, request, k=2, threshold=0.7, aspect_bar=2, embed=embed)
    assert [row["id"] for row in kept] == ["4", "2"]
    assert select_relevant(rows[:1], request, k=2, threshold=0.7, aspect_bar=2, embed=embed) == []
    assert infer_preference(select_relevant(rows[:1], request, k=2, threshold=0.7, aspect_bar=2, embed=embed))["status"] == "none"


def test_each_new_aspect_is_examined_on_its_own():
    for criterion in NEW_ASPECTS:
        rows = [
            _row(criterion, title=f"{criterion} first"),
            _row(criterion, title=f"{criterion} second"),
        ]
        preference = infer_preference(rows)
        assert preference["status"] == "applied" and preference["criterion"] == criterion
        assert emphasis_text(preference) == f"Examine {CRITERIA[criterion]['focus']} in more depth."


def test_an_option_that_mentions_the_preference_is_rejected():
    left = DraftOption(title="Hold price", thesis="Lead on integrations", supporting_signal_ids=["s1"],
                       opportunities=[DraftOpportunity(text="Win mid-market")], risks=[DraftRisk(text="A slow start")],
                       would_change_if="Fawry price per tier turns out lower")
    right = DraftOption(title="Wait", thesis="Your preference is to wait", supporting_signal_ids=["s1"],
                        opportunities=[DraftOpportunity(text="Avoid a wrong bet")], risks=[DraftRisk(text="A rival moves first")],
                        would_change_if="A verified integration cost appears")
    assert any("ranks" in item for item in problems([left, right], {"s1"}, set()))
