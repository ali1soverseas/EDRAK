"""Shape checks for the 20x10 mock accounts (no LLM)."""

from __future__ import annotations

from edrak.DecisionAnalysis.mock_accounts import build_mock_accounts
from edrak.DecisionAnalysis.personalization import infer_preference, option_themes


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


def test_mock_account_history_yields_focus_and_filtered_themes():
    account = build_mock_accounts()[0]
    preference = infer_preference(account["strategies"])
    assert preference["status"] == "applied"
    themes = option_themes(account["strategies"], preference)
    focuses = set(preference.get("criteria") or preference.get("aspects") or [])
    if preference.get("criterion"):
        focuses.add(preference["criterion"])
    assert focuses
    for theme in themes:
        assert set(theme["tags"]) & focuses
