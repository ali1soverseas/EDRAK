"""Fallback API keys: switch to the next key when one runs out of credit or is rejected."""

import threading
from collections.abc import Awaitable, Callable, Sequence

from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.providers.base import (
    ProviderNotConfigured,
    ProviderQuotaExceeded,
)

log = get_logger(__name__)


class KeyRing:
    """Keys in order of preference. The current key sticks until it fails.

    Once a key is out of credit the ring does not return to it, so a run does not pay for a
    failing call on every request. Key values never leave this object except through `current`.
    """

    def __init__(self, keys: Sequence[str]) -> None:
        self._keys = list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))
        if not self._keys:
            raise ValueError("at least one API key is needed")
        self._index = 0
        self._lock = threading.Lock()

    @property
    def current(self) -> str:
        with self._lock:
            return self._keys[self._index]

    @property
    def position(self) -> int:
        with self._lock:
            return self._index + 1

    @property
    def size(self) -> int:
        return len(self._keys)

    def rotate_from(self, used: str) -> bool:
        """Move past `used`. True when another key is now current (moved here or by a
        concurrent call), False when `used` is the last key."""
        with self._lock:
            if self._keys[self._index] != used:
                return True
            if self._index + 1 < len(self._keys):
                self._index += 1
                return True
            return False


async def with_failover[T](ring: KeyRing, provider: str, attempt: Callable[[], Awaitable[T]]) -> T:
    """Run `attempt`, moving to the next key when the current one is out of credit or rejected.

    Other failures pass straight through: a rate limit or a server error is not the key's fault.
    The last key's error is raised when every key has failed.
    """
    while True:
        used = ring.current
        try:
            return await attempt()
        except (ProviderQuotaExceeded, ProviderNotConfigured) as exc:
            if not ring.rotate_from(used):
                raise
            log.warning(
                "api_key_rotated",
                provider=provider,
                reason=type(exc).__name__,
                position=ring.position,
                of=ring.size,
            )
