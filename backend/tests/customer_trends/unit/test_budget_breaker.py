import pytest

from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetExceeded, BudgetTracker
from edrak.agents.customer_trends.schemas.common import Budget
from tests.customer_trends.factories import FakeClock


def tracker(clock: FakeClock, **limits: float) -> BudgetTracker:
    return BudgetTracker(Budget(**limits), clock=clock)  # type: ignore[arg-type]  # numeric limits


def test_tool_call_limit_is_enforced_before_the_call() -> None:
    budget = tracker(FakeClock(), max_tool_calls=2)
    for _ in range(2):
        budget.check_before_call()
        budget.record()
    with pytest.raises(BudgetExceeded) as caught:
        budget.check_before_call()
    assert caught.value.limit == "tool_calls"
    assert budget.snapshot().tool_calls == 2


def test_cost_limit_counts_the_estimate_of_the_next_call() -> None:
    budget = tracker(FakeClock(), max_cost_usd=1.0)
    budget.record(0.6)
    budget.check_before_call(0.4)
    with pytest.raises(BudgetExceeded) as caught:
        budget.check_before_call(0.5)
    assert caught.value.limit == "cost"
    budget.record(0.5)
    with pytest.raises(BudgetExceeded):
        budget.check_before_call()


def test_time_limit_uses_the_clock() -> None:
    clock = FakeClock()
    budget = tracker(clock, max_seconds=60)
    budget.check_before_call()
    clock.now += 59.9
    budget.check_before_call()
    clock.now += 0.2
    with pytest.raises(BudgetExceeded) as caught:
        budget.check_before_call()
    assert caught.value.limit == "seconds"


def test_snapshot_reports_use_and_limits_and_checks_do_not_count() -> None:
    clock = FakeClock()
    budget = tracker(clock, max_tool_calls=5, max_cost_usd=2.0, max_seconds=100)
    budget.check_before_call(0.5)
    budget.record(0.25)
    budget.record()
    clock.now += 12.5
    snapshot = budget.snapshot()
    assert (snapshot.tool_calls, snapshot.cost_usd, snapshot.seconds) == (2, 0.25, 12.5)
    assert snapshot.limits.max_tool_calls == 5


def test_breaker_opens_after_three_consecutive_failures() -> None:
    breaker = CircuitBreaker()
    for _ in range(2):
        breaker.record_failure("apify")
    assert not breaker.is_open("apify")
    breaker.record_failure("apify")
    assert breaker.is_open("apify")
    assert breaker.open_providers() == ["apify"]


def test_a_success_resets_the_failure_streak() -> None:
    breaker = CircuitBreaker()
    breaker.record_failure("apify")
    breaker.record_failure("apify")
    breaker.record_success("apify")
    breaker.record_failure("apify")
    breaker.record_failure("apify")
    assert not breaker.is_open("apify")


def test_an_open_breaker_stays_open_and_providers_are_independent() -> None:
    breaker = CircuitBreaker()
    for _ in range(3):
        breaker.record_failure("apify")
    breaker.record_success("apify")
    assert breaker.is_open("apify")
    assert not breaker.is_open("serper")
    assert breaker.open_providers() == ["apify"]
