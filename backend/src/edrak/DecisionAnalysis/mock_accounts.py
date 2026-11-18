"""Twenty mock accounts, each with ten tagged chosen strategies.

Business context is aligned with the UAE AI compute fixtures so matching can
treat history as relevant when the suite assumes business context matches.
"""

from __future__ import annotations

import json
from typing import Any

from edrak.DecisionAnalysis.personalization import CRITERIA

# Shared business context with the three UAE AI compute cross-signal fixtures.
SHARED_BUSINESS = {
    "markets": ["United Arab Emirates", "UAE"],
    "offering": "AI compute GPU and infrastructure services",
    "competitors": [{"name": "NVIDIA"}, {"name": "AMD"}, {"name": "Intel"}],
    "targets": [{"name": "sovereign AI buyers"}],
}

STRATEGY_BANK: list[dict[str, Any]] = [
    {"title": "Pilot with a local partner", "thesis": "Enter through a short joint pilot before a full footprint.", "tags": ["speed", "partnership"]},
    {"title": "Build a sovereign stack in-house", "thesis": "Own the stack and the compliance path inside the company.", "tags": ["control", "compliance"]},
    {"title": "Match competitor usage pricing", "thesis": "Compete on flexible usage-based pricing and discounts.", "tags": ["cost", "revenue"]},
    {"title": "Wait for clearer residency rules", "thesis": "Hold until data residency requirements are unambiguous.", "tags": ["risk", "compliance"]},
    {"title": "Lead on performance differentiation", "thesis": "Win on hardware leadership and distinct capability.", "tags": ["differentiation", "quality"]},
    {"title": "Scale capacity with partners", "thesis": "Grow volume through alliances and shared infrastructure.", "tags": ["scale", "partnership"]},
    {"title": "Hire a local delivery team first", "thesis": "Staff expertise before committing to a large deployment.", "tags": ["talent", "control"]},
    {"title": "Run a reversible limited offer", "thesis": "Start with a pilot that can be undone if demand is weak.", "tags": ["reversibility", "risk"]},
    {"title": "Push for earlier revenue", "thesis": "Prioritize paths that monetize sooner even if margin is thinner.", "tags": ["revenue", "speed"]},
    {"title": "Protect customer retention quality", "thesis": "Choose the path that keeps buyers through experience quality.", "tags": ["customer", "quality"]},
    {"title": "Lower total cost of ownership", "thesis": "Optimize operating cost and ROI over peak performance.", "tags": ["cost", "scale"]},
    {"title": "Move fast with proven kit", "thesis": "Ship with proven hardware to reduce failure risk.", "tags": ["speed", "risk"]},
]


def _tags_payload(tags: list[str], summary: str) -> str:
    option = [tag for tag in tags if tag in CRITERIA]
    text = summary.strip()
    if text and text[-1] not in ".!?":
        text += "."
    why = {tag: text for tag in option if text}
    return json.dumps({"version": 3, "option": option, "reason": option[:1], "why": why})


def _options_json(key: str, title: str, thesis: str) -> str:
    return json.dumps([
        {"key": key, "title": title, "thesis": thesis},
        {"key": "B" if key == "A" else "A", "title": "Alternative path", "thesis": "A different strategic bet."},
    ])


def build_mock_accounts(count: int = 20, strategies_per_account: int = 10) -> list[dict[str, Any]]:
    """Build `count` accounts with rotating strategy mixes."""
    accounts: list[dict[str, Any]] = []
    for index in range(count):
        account_id = f"mock-account-{index + 1:02d}"
        strategies: list[dict[str, Any]] = []
        for offset in range(strategies_per_account):
            template = STRATEGY_BANK[(index + offset) % len(STRATEGY_BANK)]
            key = "A" if offset % 2 == 0 else "B"
            strategy_id = f"{account_id}-s{offset + 1:02d}"
            title = f"{template['title']} ({account_id})"
            thesis = template["thesis"]
            strategies.append({
                "id": strategy_id,
                "title": title,
                "option_label": key,
                "question": "determine if entering the UAE AI compute market is advisable",
                "offering": SHARED_BUSINESS["offering"],
                "markets": json.dumps(SHARED_BUSINESS["markets"]),
                "competitors": json.dumps(SHARED_BUSINESS["competitors"]),
                "targets": json.dumps(SHARED_BUSINESS["targets"]),
                "preference_tags": _tags_payload(list(template["tags"]), thesis),
                "options_json": _options_json(key, title, thesis),
                "recorded_at": f"2026-01-{(offset % 28) + 1:02d}T12:00:00Z",
                "tags": list(template["tags"]),
                "thesis": thesis,
            })
        accounts.append({
            "id": account_id,
            "name": f"Mock Account {index + 1}",
            "email": f"mock{index + 1:02d}@company.com",
            "strategies": strategies,
        })
    return accounts


# Questions that are not about entering the UAE AI compute market.
# The other half of each account repeats the fixture goal so only that half can pass.
OFF_CONTEXT_QUESTIONS = [
    "Which grocery delivery pricing model should noon adopt in Egypt?",
    "Should GitLab enter the automotive compliance software market?",
    "How should a hospital group choose an electronic health record vendor in Germany?",
    "What packaging format should a dairy brand use for school milk in Brazil?",
    "Should a regional airline add a cargo-only route between Nairobi and Dubai?",
    "Which payroll vendor should a retail chain in Canada replace this year?",
    "How should a hotel group price weekend rooms in Barcelona?",
    "Should a textile mill in Bangladesh add a second dyeing line?",
    "What crop insurance product should a cooperative offer wheat farmers in Kansas?",
    "How should a city bus operator in Lisbon renew its diesel fleet?",
]


def _why_map(tags: list[str], summary: str) -> dict[str, str]:
    text = summary.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return {tag: text for tag in tags if tag in CRITERIA and text}


def build_focus_eval_accounts(
    question: str,
    count: int = 20,
    decisions_per_account: int = 20,
) -> list[dict[str, Any]]:
    """Twenty accounts, twenty decisions each.

    A decision is a question, tags, and a why sentence per tag. The first half
    of each account asks something else. The second half repeats `question`,
    which is the text relevance is scored on. Tags on that half rotate by account.
    """
    goal = question.strip()
    if not goal:
        raise ValueError("question is required")
    half = decisions_per_account // 2
    accounts: list[dict[str, Any]] = []
    for index in range(count):
        account_id = f"mock-account-{index + 1:02d}"
        decisions: list[dict[str, Any]] = []
        for offset in range(decisions_per_account):
            template = STRATEGY_BANK[(index + offset) % len(STRATEGY_BANK)]
            tags = [tag for tag in template["tags"] if tag in CRITERIA]
            why = _why_map(tags, template["thesis"])
            on_context = offset >= half
            asked = goal if on_context else OFF_CONTEXT_QUESTIONS[(index + offset) % len(OFF_CONTEXT_QUESTIONS)]
            decisions.append({
                "id": f"{account_id}-d{offset + 1:02d}",
                "question": asked,
                "tags": tags,
                "why": why,
                "preference_tags": json.dumps({
                    "version": 3,
                    "option": tags,
                    "reason": tags[:1],
                    "why": why,
                }),
                "recorded_at": f"2026-01-{(offset % 28) + 1:02d}T12:00:00Z",
            })
        accounts.append({
            "id": account_id,
            "decisions": decisions,
        })
    return accounts
