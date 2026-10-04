"""Per-run budget: tool calls, cost and wall-clock time."""

import threading
import time
from collections.abc import Callable
from typing import Literal

from edrak.agents.customer_trends.schemas.common import Budget, BudgetSnapshot


class BudgetExceeded(Exception):
    def __init__(self, limit: Literal["tool_calls", "cost", "seconds"], message: str) -> None:
        super().__init__(message)
        self.limit = limit


class BudgetTracker:
    """Refuses a call that would cross a limit; records what each call used."""

    def __init__(self, budget: Budget, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._budget = budget
        self._clock = clock
        self._started = clock()
        self._tool_calls = 0
        self._cost_usd = 0.0
        self._lock = threading.Lock()

    def check_before_call(self, estimated_cost: float = 0.0) -> None:
        with self._lock:
            limits = self._budget
            if self._tool_calls + 1 > limits.max_tool_calls:
                raise BudgetExceeded(
                    "tool_calls", f"tool call limit reached ({limits.max_tool_calls})"
                )
            if self._cost_usd + estimated_cost > limits.max_cost_usd:
                raise BudgetExceeded("cost", f"cost limit reached ({limits.max_cost_usd:.2f} USD)")
            if self._clock() - self._started >= limits.max_seconds:
                raise BudgetExceeded("seconds", f"time limit reached ({limits.max_seconds:g} s)")

    def record(self, cost: float = 0.0) -> None:
        """Count one tool call and its cost."""
        with self._lock:
            self._tool_calls += 1
            self._cost_usd += cost

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(
                tool_calls=self._tool_calls,
                cost_usd=self._cost_usd,
                seconds=self._clock() - self._started,
                limits=self._budget,
            )
