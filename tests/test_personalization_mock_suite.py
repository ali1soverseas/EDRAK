"""Shape checks for the 20x10 mock accounts (no LLM)."""

from __future__ import annotations

from edrak.DecisionAnalysis.mock_accounts import build_mock_accounts
from edrak.DecisionAnalysis.personalization import criteria_focus, infer_preference


def test_mock_suite_has_twenty_accounts_with_ten_strategies_each():
    accounts = build_mock_accounts()
    assert len(accounts) == 20
    for account in accounts:
        assert len(account["strategies"]) == 10
        assert account["email"].endswith("@company.com")
        for row in account["strategies"]:
            assert row["preference_tags"]
            assert row["options_json"]
            assert row["tags"]


def test_mock_account_history_yields_a_preference_and_criteria_focus():
    account = build_mock_accounts()[0]
    preference = infer_preference(account["strategies"])
    assert preference["status"] == "applied"
    focus = criteria_focus(account["strategies"])
    assert focus
    assert all(item["items"] for item in focus)
