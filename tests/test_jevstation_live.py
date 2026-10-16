"""Live JEVStation smoke test. Runs ONLY when explicitly enabled.

Requires (never committed, never printed):
    JEVSTATION_API_KEY  - real key, exported in the shell
    JEVSTATION_LIVE=1   - explicit opt-in flag

Cost: one standard evaluation (1 credit) plus a free balance check.
Run:  JEVSTATION_LIVE=1 python -m pytest tests/test_jevstation_live.py -s
"""

import os

import pytest

from edrak.eval.jevstation import JevStationClient, NoulAnswer, noul_question

LIVE = os.environ.get("JEVSTATION_LIVE") == "1" and bool(os.environ.get("JEVSTATION_API_KEY"))

pytestmark = pytest.mark.skipif(not LIVE, reason="Set JEVSTATION_LIVE=1 with JEVSTATION_API_KEY to run.")


def test_live_single_noul_smoke():
    with JevStationClient() as client:  # key comes from the environment only
        before = client.get_credits()
        print(f"\nbalance before: {before}")
        result = client.evaluate(
            "The sky is blue.",
            {"sanity": noul_question("Is the sky described as blue in this text?")},
        )
        after = client.get_credits()
        print(f"balance after: {after}")

    answer = result.answers["sanity"]
    assert isinstance(answer, NoulAnswer)
    assert 0.0 <= answer.noul <= 1.0
    assert answer.noul > 0.5, "a trivially true statement should score above one half"
    assert result.credits_charged == 1, "a 1-question short-state call must cost 1 credit"
    print(f"model={result.model} noul={answer.noul} charged={result.credits_charged} "
          f"remaining={result.credits_remaining}")
