"""Centralized LLM usage tracking.

Every model request is recorded once by a LangChain callback attached in
``edrak.core.llm.get_chat_model``: tokens, latency, throughput and estimated
cost. Records are appended to ``usage/logs/llm_calls.jsonl`` by a background
thread, so tracking never blocks a workflow and never changes a model reply.

    from edrak.usage import usage_context, report

    with usage_context(run_id="req-1", stage="competitor"):
        ...
    print(report.render_workflow("req-1"))
"""

from __future__ import annotations

from .context import UsageContext, current, usage_context, use_case_value
from .pricing import estimate_cost, price_for
from .store import emit, enabled, flush, log_dir, read_records

__all__ = [
    "UsageContext",
    "current",
    "emit",
    "enabled",
    "estimate_cost",
    "flush",
    "log_dir",
    "price_for",
    "read_records",
    "usage_context",
    "use_case_value",
]
