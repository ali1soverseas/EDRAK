"""Per-run circuit breaker: a provider that keeps failing is skipped for the rest of the run."""

import threading
from collections import defaultdict

FAILURE_THRESHOLD = 3


class CircuitBreaker:
    def __init__(self, threshold: int = FAILURE_THRESHOLD) -> None:
        self._threshold = threshold
        self._consecutive: defaultdict[str, int] = defaultdict(int)
        self._open: set[str] = set()
        self._lock = threading.Lock()

    def record_failure(self, provider: str) -> None:
        with self._lock:
            self._consecutive[provider] += 1
            if self._consecutive[provider] >= self._threshold:
                self._open.add(provider)

    def record_success(self, provider: str) -> None:
        with self._lock:
            if provider not in self._open:
                self._consecutive[provider] = 0

    def is_open(self, provider: str) -> bool:
        with self._lock:
            return provider in self._open

    def failures(self, provider: str) -> int:
        """Failures in a row, since the provider last succeeded."""
        with self._lock:
            return self._consecutive[provider]

    def open_providers(self) -> list[str]:
        with self._lock:
            return sorted(self._open)
