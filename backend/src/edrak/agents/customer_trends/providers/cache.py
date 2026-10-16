"""Disk cache of provider results, for development and replay. Errors are never cached."""

import hashlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from edrak.agents.customer_trends.providers.base import ProviderResult

if TYPE_CHECKING:
    from edrak.agents.customer_trends.settings import Settings

_RUN_SCOPED_KEYS = frozenset({"run_id", "task_id"})


def cache_key(provider: str, capability: str, params: dict[str, Any]) -> str:
    """sha256 over the provider, the capability and the canonical params.

    `run_id` and `task_id` are left out: the same query in another run is the same request.
    """
    canonical = json.dumps(
        {k: v for k, v in params.items() if k not in _RUN_SCOPED_KEYS},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(f"{provider}|{capability}|{canonical}".encode()).hexdigest()


class DiskCache:
    def __init__(self, root: Path, ttl_s: float, *, clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self._ttl_s = ttl_s
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: "Settings") -> "DiskCache":
        return cls(settings.data_dir / "cache", settings.edrak_cache_ttl_s)

    def _path(self, provider: str, capability: str, params: dict[str, Any]) -> Path:
        return self._root / f"{cache_key(provider, capability, params)}.json"

    def get(self, provider: str, capability: str, params: dict[str, Any]) -> ProviderResult | None:
        if self._ttl_s <= 0:
            return None
        path = self._path(provider, capability, params)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if self._clock() - float(stored["stored_at"]) > self._ttl_s:
                path.unlink(missing_ok=True)
                return None
            return ProviderResult.model_validate(stored["result"])
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            return None

    def put(
        self, provider: str, capability: str, params: dict[str, Any], result: ProviderResult
    ) -> None:
        """Store a complete result. Partial results are not cached."""
        if self._ttl_s <= 0 or result.partial:
            return
        path = self._path(provider, capability, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"stored_at": self._clock(), "result": result.model_dump(mode="json")}
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, path)
