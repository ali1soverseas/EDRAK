"""The callback that records every model request.

Attached in the model factory (``edrak.core.llm.get_chat_model``), so it fires
however the caller invokes the model - a plain ``invoke``, a
``with_structured_output`` run, a retry, a repair loop - with no change at any
call site.

The handler never raises and never blocks: LangChain calls it on the calling
thread, so it does the minimum (read the reply, queue a line) and hands the
write to ``store``'s background thread. Prompts, replies and credentials are
never recorded.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import BaseCallbackHandler

from . import context as _context
from .pricing import estimate_cost
from .record import LLMCall
from .store import emit, enabled

def _provider() -> str:
    """The provider recorded on every row: LLM_PROVIDER when set, else ollama."""
    return os.getenv("LLM_PROVIDER", "").strip().lower() or "ollama"


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_ms_from_ns(value: Any) -> float | None:
    number = _as_int(value)
    if number is None:
        return None
    return round(number / 1_000_000, 3)


def _first_message(response: Any) -> Any | None:
    """The first generated message.

    ``on_llm_end`` is handed an ``LLMResult`` whose generations are nested
    (``[[ChatGeneration]]``); a bare ``ChatResult`` puts them flat. Accept both.
    """
    generations = getattr(response, "generations", None)
    if not generations:
        return None
    first = generations[0]
    if isinstance(first, (list, tuple)):
        if not first:
            return None
        first = first[0]
    return getattr(first, "message", None) or getattr(first, "text", None)


def _model_from_kwargs(kwargs: dict[str, Any]) -> str | None:
    params = kwargs.get("invocation_params") or {}
    model = params.get("model") if isinstance(params, dict) else None
    if isinstance(model, str) and model:
        return model
    metadata = kwargs.get("metadata") or {}
    if isinstance(metadata, dict):
        name = metadata.get("ls_model_name")
        if isinstance(name, str) and name:
            return name
    return None


def _node_from_kwargs(kwargs: dict[str, Any]) -> str | None:
    """The LangGraph node the call was made from, when there is one.

    LangGraph puts the innermost node name in the run metadata, so a call made
    deep inside a worker graph reports that worker's own node (``stage_1``,
    ``synthesize``) rather than the outer dispatch step. This is what gives the
    report its worker sub-stage without the worker having to say anything.
    """
    metadata = kwargs.get("metadata") or {}
    if isinstance(metadata, dict):
        node = metadata.get("langgraph_node")
        if isinstance(node, str) and node:
            return node
    return None


class UsageCallback(BaseCallbackHandler):
    """Records one row per model request. Never raises into the workflow."""

    # BaseCallbackHandler already defaults to False; stated here so it is not
    # changed by accident later.
    raise_error = False

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._open: dict[str, dict[str, Any]] = {}

    # -- lifecycle ---------------------------------------------------------
    def on_chat_model_start(
        self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._begin(run_id, kwargs)

    def on_llm_start(
        self, serialized: Any, prompts: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._begin(run_id, kwargs)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            self._finish(response, run_id)
        except Exception:
            logger.debug("Could not record a completed model call", exc_info=True)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            self._fail(error, run_id)
        except Exception:
            logger.debug("Could not record a failed model call", exc_info=True)

    # -- internals ---------------------------------------------------------
    def _begin(self, run_id: UUID, kwargs: dict[str, Any]) -> None:
        if not enabled():
            return
        state = {
            "t0": time.perf_counter(),
            "wall": _now_iso(),
            "model": _model_from_kwargs(kwargs),
            "node": _node_from_kwargs(kwargs),
            "ctx": _context.current(),
        }
        with self._lock:
            self._open[str(run_id)] = state

    def _pop(self, run_id: UUID) -> dict[str, Any]:
        with self._lock:
            return self._open.pop(str(run_id), {})

    def _finish(self, response: Any, run_id: UUID) -> None:
        state = self._pop(run_id)
        if not state and not enabled():
            return
        ctx = state.get("ctx") or _context.current()
        elapsed_ms = _elapsed(state)

        message = _first_message(response)
        usage = getattr(message, "usage_metadata", None)
        usage = usage if isinstance(usage, dict) else {}
        meta = getattr(message, "response_metadata", None)
        meta = meta if isinstance(meta, dict) else {}

        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        # Fall back to Ollama's own counters when usage_metadata is absent.
        if input_tokens is None:
            input_tokens = _as_int(meta.get("prompt_eval_count"))
        if output_tokens is None:
            output_tokens = _as_int(meta.get("eval_count"))
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

        in_details = usage.get("input_token_details") or {}
        out_details = usage.get("output_token_details") or {}
        cached_input_tokens = _as_int(in_details.get("cache_read")) if isinstance(in_details, dict) else None
        reasoning_tokens = _as_int(out_details.get("reasoning")) if isinstance(out_details, dict) else None

        generation_ms = _as_ms_from_ns(meta.get("eval_duration"))
        provider_ms = _as_ms_from_ns(meta.get("total_duration"))
        generation_tps = None
        if output_tokens and generation_ms:
            generation_tps = round(output_tokens / (generation_ms / 1000), 3)

        model = state.get("model") or meta.get("model")
        model = str(model) if model else None
        cost, known, source = estimate_cost(
            _provider(), model, input_tokens, output_tokens, cached_input_tokens
        )

        self._write(
            LLMCall(
                call_id=str(run_id),
                run_id=ctx.run_id,
                use_case=ctx.use_case,
                stage=ctx.stage,
                # An explicit call-site label wins; otherwise the LangGraph node
                # the call came from names the sub-stage.
                operation=ctx.operation or state.get("node"),
                provider=_provider(),
                model=model,
                timestamp=state.get("wall") or _now_iso(),
                retry_attempt=max(0, (ctx.attempt or 1) - 1),
                status="success",
                error_type=None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
                latency_ms=provider_ms if provider_ms is not None else elapsed_ms,
                app_elapsed_ms=elapsed_ms,
                generation_ms=generation_ms,
                generation_tps=generation_tps,
                estimated_cost_usd=cost,
                pricing_known=known,
                pricing_source=source,
            )
        )

    def _fail(self, error: BaseException, run_id: UUID) -> None:
        state = self._pop(run_id)
        if not state and not enabled():
            return
        ctx = state.get("ctx") or _context.current()
        self._write(
            LLMCall(
                call_id=str(run_id),
                run_id=ctx.run_id,
                use_case=ctx.use_case,
                stage=ctx.stage,
                # An explicit call-site label wins; otherwise the LangGraph node
                # the call came from names the sub-stage.
                operation=ctx.operation or state.get("node"),
                provider=_provider(),
                model=state.get("model"),
                timestamp=state.get("wall") or _now_iso(),
                retry_attempt=max(0, (ctx.attempt or 1) - 1),
                status="failure",
                error_type=type(error).__name__,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                cached_input_tokens=None,
                reasoning_tokens=None,
                latency_ms=_elapsed(state),
                app_elapsed_ms=_elapsed(state),
                generation_ms=None,
                generation_tps=None,
                estimated_cost_usd=None,
                pricing_known=False,
                pricing_source=None,
            )
        )

    @staticmethod
    def _write(call: LLMCall) -> None:
        emit(call.to_dict())


def _elapsed(state: dict[str, Any]) -> float | None:
    t0 = state.get("t0")
    if t0 is None:
        return None
    return round((time.perf_counter() - t0) * 1000, 3)


_callback: UsageCallback | None = None
_callback_lock = threading.Lock()


def usage_callback() -> UsageCallback:
    """The shared handler every model is built with. Cheap and thread-safe."""
    global _callback
    if _callback is None:
        with _callback_lock:
            if _callback is None:
                _callback = UsageCallback()
    return _callback


def new_call_id() -> str:
    """A fallback identity when a caller has no LangChain run id to hand."""
    return str(uuid4())
