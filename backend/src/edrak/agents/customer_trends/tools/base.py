"""Shared machinery of the collection tools: the context, input handling and the executor.

A tool never raises. A bad argument, a failing provider, an exhausted budget and a bug all
come back as a `ToolResponse` with `status="error"` and an `error_code`, so the model can read
what went wrong and carry on. The processing tools answer with their own `ProcessingResponse`
on success and the same error `ToolResponse` on failure.
"""

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.providers.base import ProviderExhausted, ProviderResult
from edrak.agents.customer_trends.providers.breaker import CircuitBreaker
from edrak.agents.customer_trends.providers.budget import BudgetExceeded, BudgetTracker
from edrak.agents.customer_trends.providers.registry import ProviderRegistry
from edrak.agents.customer_trends.schemas.common import (
    Depth,
    ProcessingResponse,
    ToolResponse,
    ToolStatus,
    effective_max_results,
)
from edrak.agents.customer_trends.schemas.evidence import EvidenceFilters, EvidenceItem
from edrak.agents.customer_trends.settings import Settings
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.utils.text import truncate

log = get_logger(__name__)

# What a call asks for when the model gives no `max_results`; the depth cap still applies.
DEFAULT_RESULTS = {"web": 10, "social": 30, "reviews": 30, "news": 25}
PREVIEW_ITEMS = 5
_ARG_SUMMARY_CHARS = 80
_ARG_SUMMARY_ITEMS = 5

Emitter = Callable[[dict[str, Any]], None]


def _no_emit(event: dict[str, Any]) -> None:
    return None


@dataclass
class ToolContext:
    """Everything a tool needs. One per run; `tools_for_branch` makes a copy per branch."""

    settings: Settings
    store: EvidenceStore
    providers: ProviderRegistry
    budget: BudgetTracker
    breaker: CircuitBreaker
    run_id: str
    task_id: str
    emit: Emitter = _no_emit
    # Brief values (languages, geo, since, until, depth, entity) used when the model omits them.
    defaults: dict[str, Any] = field(default_factory=dict)
    branch: str | None = None
    # The model analyze_text calls; built from the settings for the analyst role when unset.
    analyst: BaseChatModel | None = None

    def for_branch(self, branch: str) -> "ToolContext":
        return replace(self, branch=branch)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    run: Callable[[ToolContext, Any], Awaitable[ToolResponse | ProcessingResponse]]


@dataclass(frozen=True)
class StoredBatch:
    """What persisting a provider result produced."""

    batch_id: str | None
    inserted: int
    duplicates: int
    preview: list[dict[str, Any]]
    coverage: dict[str, Any]
    extra_warnings: list[str] = field(default_factory=list)
    extra_gaps: list[str] = field(default_factory=list)


Persist = Callable[[ToolContext, str, ProviderResult], StoredBatch]


def results_limit(depth: Depth, requested: int | None, default: int) -> int:
    """How many results to ask a provider for: the request or the tool default, capped by depth."""
    return effective_max_results(depth, requested if requested is not None else default)


def summarize_args(inp: BaseModel) -> dict[str, Any]:
    """The non-default arguments of a call, shortened for an event."""
    return shorten(
        inp.model_dump(mode="json", exclude_defaults=True, exclude={"run_id", "task_id"})
    )


def shorten(raw: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            value = truncate(value, _ARG_SUMMARY_CHARS)
        elif isinstance(value, list):
            value = value[:_ARG_SUMMARY_ITEMS]
        summary[key] = value
    return summary


def emit_event(
    ctx: ToolContext,
    tool: str,
    args: dict[str, Any],
    *,
    status: ToolStatus,
    count: int,
    started: float,
    provider: str | None = None,
    fallback_used: bool = False,
    cost: float = 0.0,
    error_code: str | None = None,
) -> None:
    event = {
        "type": "tool_called",
        "tool": tool,
        "branch": ctx.branch,
        "run_id": ctx.run_id,
        "task_id": ctx.task_id,
        "args": args,
        "provider": provider,
        "fallback_used": fallback_used,
        "status": status.value,
        "count": count,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "cost": cost,
        "error_code": error_code,
    }
    try:
        ctx.emit(event)
    except Exception:
        log.warning("event_emitter_failed", tool=tool)


def _emit(
    ctx: ToolContext, tool: str, args: dict[str, Any], response: ToolResponse, started: float
) -> None:
    emit_event(
        ctx,
        tool,
        args,
        status=response.status,
        count=response.count,
        started=started,
        provider=response.provider_used,
        fallback_used=response.fallback_used,
        cost=response.cost_estimate,
        error_code=response.error_code,
    )


def finish_processing[R: ToolResponse | ProcessingResponse](
    ctx: ToolContext, tool: str, inp: BaseModel, response: R, started: float
) -> R:
    """Emit the `tool_called` event of a processing tool and hand its response back."""
    emit_event(
        ctx,
        tool,
        summarize_args(inp),
        status=response.status,
        count=response.count,
        started=started,
        error_code=response.error_code if isinstance(response, ToolResponse) else None,
    )
    return response


def error_response(code: str, message: str, **fields: Any) -> ToolResponse:
    return ToolResponse(status=ToolStatus.ERROR, error_code=code, gaps=[message], **fields)


def invalid_input_response(exc: ValidationError, prefix: str = "") -> ToolResponse:
    problems = [
        f"{prefix}{'.'.join(str(part) for part in error['loc']) or 'input'}: {error['msg']}"
        for error in exc.errors()
    ]
    return error_response("invalid_input", "invalid input: " + "; ".join(problems))


def _failure_summary(exc: ProviderExhausted) -> str:
    parts = [
        f"{failure.provider} {'skipped' if failure.skipped else 'failed'} ({failure.error})"
        for failure in exc.failures
    ]
    return f"{exc.capability}: no provider could serve it: " + ", ".join(parts or ["none routed"])


def evidence_preview(store: EvidenceStore, run_id: str, batch_id: str) -> list[dict[str, Any]]:
    """The most engaged new items, as short pointers (id, platform, snippet, url)."""
    found = store.query(run_id, EvidenceFilters(batch_ids=[batch_id]), limit=PREVIEW_ITEMS)
    return [
        {
            "id": item.id,
            "platform": item.platform.value if item.platform else item.source_type.value,
            "language": item.language,
            "snippet": item.text,
            "url": item.url,
        }
        for item in found.items
    ]


def batch_coverage(store: EvidenceStore, run_id: str, batch_id: str) -> dict[str, Any]:
    """New items by language, platform and source type. Items that belong to no platform
    (articles, reviews, trend points) show up under their source type only."""
    platforms = store.count_by(run_id, "platform", [batch_id])
    platforms.pop("unknown", None)
    return {
        "languages": store.count_by(run_id, "language", [batch_id]),
        "platforms": platforms,
        "source_types": store.count_by(run_id, "source_type", [batch_id]),
    }


def persist_evidence(ctx: ToolContext, tool: str, result: ProviderResult) -> StoredBatch:
    items = [item for item in result.items if isinstance(item, EvidenceItem)]
    batch_id, inserted, duplicates = ctx.store.add_batch(
        ctx.run_id, ctx.task_id, tool, items, {"provider": result.provider, **result.meta}
    )
    if inserted == 0:
        return StoredBatch(None, 0, duplicates, [], {})
    return StoredBatch(
        batch_id,
        inserted,
        duplicates,
        evidence_preview(ctx.store, ctx.run_id, batch_id),
        batch_coverage(ctx.store, ctx.run_id, batch_id),
    )


def collection_gaps(
    stored: StoredBatch, result: ProviderResult, languages: Sequence[str]
) -> list[str]:
    gaps: list[str] = []
    if not result.items:
        gaps.append("no results were returned for this request")
    elif stored.inserted == 0:
        gaps.append(f"all {stored.duplicates} item(s) were already collected in this run")
    else:
        found = set(stored.coverage.get("languages", {}))
        gaps.extend(f"no {code} items in this batch" for code in languages if code not in found)
    if any(isinstance(i, EvidenceItem) and i.snippet_only for i in result.items):
        gaps.append("snippet-only data: search snippets without the full text or engagement counts")
    return [*gaps, *stored.extra_gaps]


async def execute_collection(
    ctx: ToolContext,
    tool: str,
    capability: str,
    inp: BaseModel,
    params: dict[str, Any],
    *,
    languages: Sequence[str] = (),
    persist: Persist = persist_evidence,
    started: float | None = None,
) -> ToolResponse:
    """Run one collection capability and turn the outcome into a `ToolResponse`.

    Order: call the provider registry (which checks the budget, routes, falls back and records
    cost), store the items as one batch, compute coverage and gaps, build the response with at
    most five preview items, emit a `tool_called` event. Never raises.
    """
    started = started if started is not None else time.perf_counter()
    args = summarize_args(inp)
    try:
        result = await ctx.providers.call(
            capability, {**params, "run_id": ctx.run_id, "task_id": ctx.task_id}
        )
        stored = persist(ctx, tool, result)
        response = ToolResponse(
            status=ToolStatus.PARTIAL if result.partial else ToolStatus.OK,
            batch_id=stored.batch_id,
            count=stored.inserted,
            preview=stored.preview,
            provider_used=result.provider,
            fallback_used=result.fallback_used,
            coverage=stored.coverage,
            gaps=collection_gaps(stored, result, languages),
            warnings=[*result.warnings, *stored.extra_warnings],
            cost_estimate=result.cost_estimate,
            next_cursor=result.next_cursor,
        )
    except BudgetExceeded as exc:
        response = error_response("budget_exceeded", f"budget exceeded: {exc}")
    except ProviderExhausted as exc:
        response = error_response("provider_exhausted", _failure_summary(exc))
    except Exception as exc:
        log.error("tool_failed", tool=tool, error=type(exc).__name__)
        response = error_response("tool_error", f"{tool} failed unexpectedly: {type(exc).__name__}")
    _emit(ctx, tool, args, response, started)
    return response


async def invoke_tool(
    ctx: ToolContext, spec: ToolSpec, raw: dict[str, Any]
) -> ToolResponse | ProcessingResponse:
    """Validate a model's arguments, inject the run ids and brief defaults, and run the tool."""
    started = time.perf_counter()
    known = spec.input_model.model_fields
    arguments = {
        **{k: v for k, v in ctx.defaults.items() if k in known},
        **raw,
        "run_id": ctx.run_id,
        "task_id": ctx.task_id,
    }
    try:
        inp = spec.input_model.model_validate(arguments)
    except ValidationError as exc:
        response = invalid_input_response(exc)
        _emit(ctx, spec.name, shorten(raw), response, started)
        return response
    try:
        return await spec.run(ctx, inp)
    except Exception as exc:
        log.error("tool_failed", tool=spec.name, error=type(exc).__name__)
        response = error_response(
            "tool_error", f"{spec.name} failed unexpectedly: {type(exc).__name__}"
        )
        _emit(ctx, spec.name, summarize_args(inp), response, started)
        return response
