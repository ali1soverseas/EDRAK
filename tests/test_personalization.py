"""Decision tags and the emphasis they produce. No model calls."""

import json

from edrak.DecisionAnalysis.nodes import build_decision_analysis_user_prompt
from edrak.DecisionAnalysis.options import _prompt, problems, DraftOption, DraftOpportunity, DraftRisk
from edrak.DecisionAnalysis.personalization import CRITERIA, infer_preference, note_text, tag_decision

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

    # Equal weight across two aspects applies both focuses (no longer "uncertain").
    split = infer_preference([_row("speed"), _row("speed", title="Other"), _row("cost"), _row("cost", title="Again")])
    assert split["status"] == "applied" and split["criteria"] == ["speed", "cost"]

    # One stated reason outweighs one option tag, but still needs a second decision.
    weighted = infer_preference([_row("speed", reason=True), _row("cost"), _row("speed", title="Second")])
    assert weighted["status"] == "applied" and weighted["criterion"] == "speed"


def test_the_old_emphasis_sentence_stays_off_both_prompts():
    assert note_text({"status": "none"}) is None
    assert note_text(None) is None
    speed = "Examine time to market, implementation time and integration effort in more depth."
    context = {"business_goal": "Choose a billing approach", "business_request": {}, "decision_questions": ["Build or buy?"],
               "signals": [], "cross_signal": {}, "analysis_emphasis": speed}
    assert "analysis_emphasis" not in build_decision_analysis_user_prompt(context)
    assert "implementation time" not in build_decision_analysis_user_prompt(context)
    bare = {"business_goal": "Choose a billing approach", "business_request": {}, "signals": [], "analysis_emphasis": speed,
            "history_option_themes": [{"title": "Pilot", "thesis": "Ship this quarter"}]}
    prompt = _prompt(bare)
    assert "ANALYSIS EMPHASIS" not in prompt and "STRATEGY THEMES" not in prompt and "implementation time" not in prompt
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
    assert "time to value" in note_text(applied)
    assert "ANALYSIS EMPHASIS" not in _prompt({"business_goal": "Choose a billing approach", "business_request": {}, "signals": []})


def test_a_split_across_decisions_applies_both_focuses():
    split = infer_preference([_row("speed"), _row("speed", title="Other"), _row("cost"), _row("cost", title="Again")])
    assert split["status"] == "applied" and split["criteria"] == ["speed", "cost"]
    assert "implementation time and total cost" in note_text(split)


def test_three_cleared_aspects_are_examined_together():
    rows = [
        _row("speed"), _row("speed", title="S2"),
        _row("cost"), _row("cost", title="C2"),
        _row("risk"), _row("risk", title="R2"),
    ]
    applied = infer_preference(rows)
    assert applied["status"] == "applied"
    assert set(applied["criteria"]) == {"speed", "cost", "risk"}
    assert "implementation time" in note_text(applied) and "reliability and risk" in note_text(applied)


def test_a_weaker_aspect_on_other_decisions_leaves_the_leader():
    applied = infer_preference([
        _row("speed"), _row("speed", title="Second"), _row("speed", title="Third"), _row("cost", title="Once"),
    ])
    assert applied["status"] == "applied" and applied["criterion"] == "speed"


def test_an_aspect_must_clear_the_bar_by_score_or_count():
    assert infer_preference([_row("speed")], aspect_bar=2)["status"] == "none"
    assert infer_preference([_row("speed", reason=True)], aspect_bar=2)["criterion"] == "speed"
    assert infer_preference([_row("speed"), _row("speed", title="Billing")], aspect_bar=2)["criterion"] == "speed"


def test_select_relevant_averages_strategy_and_business_scores():
    from edrak.DecisionAnalysis.personalization import select_relevant

    vectors = {
        "uae": [1.0, 0.0],
        "close": [0.95, 0.05],
        "mid": [0.8, 0.6],
        "far": [0.0, 1.0],
        "empty": [0.0, 0.0],
    }

    def embed(texts):
        out = []
        for text in texts:
            key = next((name for name in vectors if name and name in text), "empty" if not text.strip() else "uae")
            out.append(vectors[key])
        return out

    # Dual texts: [req_strategy, req_business, past_strategy, past_business, ...]
    request = {"goal": "uae market entry", "business_context": {"markets": ["uae"]}}
    rows = [
        {"id": "1", "title": "far billing", "question": "far", "markets": '["uae"]', "recorded_at": "2026-03-01", "preference_tags": "{}"},
        {"id": "2", "title": "close entry", "question": "close", "markets": '["uae"]', "recorded_at": "2026-01-01", "preference_tags": "{}"},
        {"id": "3", "title": "mid entry", "question": "mid", "markets": '["uae"]', "recorded_at": "2026-02-01", "preference_tags": "{}"},
        {"id": "4", "title": "closer entry", "question": "close", "markets": '["uae"]', "recorded_at": "2026-04-01", "preference_tags": "{}"},
    ]
    kept = select_relevant(rows, request, k=2, threshold=0.7, aspect_bar=2, embed=embed)
    assert [row["id"] for row in kept] == ["4", "2"]
    assert select_relevant(rows[:1], request, k=2, threshold=0.7, aspect_bar=2, embed=embed) == []
    assert infer_preference(select_relevant(rows[:1], request, k=2, threshold=0.7, aspect_bar=2, embed=embed))["status"] == "none"


def test_each_new_aspect_is_named_in_the_note():
    for criterion in NEW_ASPECTS:
        rows = [
            _row(criterion, title=f"{criterion} first"),
            _row(criterion, title=f"{criterion} second"),
        ]
        preference = infer_preference(rows)
        assert preference["status"] == "applied" and preference["criterion"] == criterion
        assert note_text(preference) == (
            f"This analysis looks more closely at {CRITERIA[criterion]['label']}, "
            f"based on earlier decisions: {criterion} first, {criterion} second."
        )


def test_a_tag_keeps_the_phrase_that_selected_it():
    options = [
        _option("A", "Launch fast", "Ship with a partner this quarter", opportunities=["Reach the market sooner"]),
        _option("B", "Build in-house", "Staff a team and keep ownership", opportunities=["Long-term control"]),
        _option("C", "Wait", "Close the evidence gap before committing"),
    ]
    tags = tag_decision(options, "A", "It has to stay compliant")
    assert tags["version"] == 3
    assert tags["option"] == ["speed"] and tags["reason"] == ["compliance"]
    assert tags["why"]["speed"] == "Launch fast. Reach the market sooner."
    assert tags["why"]["compliance"] == "It has to stay compliant."

    both = tag_decision(options, "A", "We need this live before the deadline")
    assert both["why"]["speed"] == "Launch fast. We need this live before the deadline."


def test_criteria_focus_is_the_only_personalization_in_the_options_prompt():
    from edrak.DecisionAnalysis.personalization import criteria_focus

    rows = [{
        "id": "1",
        "title": "Portal",
        "preference_tags": json.dumps({
            "version": 3,
            "option": ["speed"],
            "reason": ["compliance"],
            "why": {"speed": "Ship with a partner this quarter.", "compliance": "It has to stay compliant.", "cost": ""},
        }),
    }, {
        "id": "2",
        "title": "Old",
        "preference_tags": json.dumps({"version": 2, "option": ["speed"], "reason": []}),
    }]
    focus = criteria_focus(rows)
    assert focus == [{
        "id": "1",
        "title": "Portal",
        "items": [
            {"criterion": "speed", "summary": "Ship with a partner this quarter."},
            {"criterion": "compliance", "summary": "It has to stay compliant."},
        ],
    }]
    prompt = _prompt({
        "business_goal": "Choose a billing approach",
        "business_request": {},
        "signals": [],
        "analysis_emphasis": "Examine implementation time in more depth.",
        "history_option_themes": [{"title": "Pilot", "thesis": "Ship this quarter"}],
        "criteria_focus": focus,
    })
    assert "CRITERIA FOCUS FROM SIMILAR PAST DECISIONS:" in prompt
    assert "- speed: Ship with a partner this quarter." in prompt
    assert "- compliance: It has to stay compliant." in prompt
    assert "ANALYSIS EMPHASIS" not in prompt and "STRATEGY THEMES" not in prompt
    assert "CRITERIA FOCUS" not in _prompt({"business_goal": "Choose a billing approach", "business_request": {}, "signals": []})


def test_an_option_that_mentions_the_preference_is_rejected():
    left = DraftOption(title="Hold price", thesis="Lead on integrations", supporting_signal_ids=["s1"],
                       opportunities=[DraftOpportunity(text="Win mid-market")], risks=[DraftRisk(text="A slow start")],
                       would_change_if="Fawry price per tier turns out lower")
    right = DraftOption(title="Wait", thesis="Your preference is to wait", supporting_signal_ids=["s1"],
                        opportunities=[DraftOpportunity(text="Avoid a wrong bet")], risks=[DraftRisk(text="A rival moves first")],
                        would_change_if="A verified integration cost appears")
    assert any("ranks" in item for item in problems([left, right], {"s1"}, set()))
