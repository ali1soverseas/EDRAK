"""The shape of one recorded model request.

One record is one actual request attempt. A retry is a new record, not an edit
of the previous one, so the attempts a run spent tokens on stay visible. Fields
the provider does not expose are ``None`` rather than zero, so a report can tell
"not reported" from "zero".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class LLMCall:
    # Identification
    call_id: str
    run_id: str | None
    use_case: str | None
    stage: str | None
    operation: str | None
    provider: str
    model: str | None
    timestamp: str
    retry_attempt: int

    # Outcome
    status: str
    error_type: str | None

    # Token consumption (provider-reported where available)
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None

    # Performance. latency_ms is end to end; app_elapsed_ms is measured by us;
    # generation_ms/tps come from the provider's own evaluation durations.
    latency_ms: float | None
    app_elapsed_ms: float | None
    generation_ms: float | None
    generation_tps: float | None

    # Cost
    estimated_cost_usd: float | None
    pricing_known: bool
    pricing_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
