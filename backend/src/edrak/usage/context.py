"""Which run, stage and use case the current model call belongs to.

Mirrors ``edrak.core.action_log``: a ``ContextVar`` set at a workflow boundary
and read by the usage callback. LangGraph runs sync nodes in a thread pool, and
contextvars do not follow a call into an executor thread, so the context is set
where the call actually happens (``run_task`` and each node), not only in the
requesting coroutine.

Absent fields inherit from the enclosing context, so a worker can set its stage
while the run id set higher up flows through unchanged.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageContext:
    run_id: str | None = None
    use_case: str | None = None
    stage: str | None = None
    # The specific thing being asked of the model: a call site outside the
    # workflow (``scope_check``, ``eval_faithfulness``) or a sub-phase inside a
    # worker (``discovery``, ``stage_1``). ``stage`` answers "which worker";
    # this answers "which part of it".
    operation: str | None = None
    attempt: int = 1


_default = UsageContext()
_current: contextvars.ContextVar[UsageContext] = contextvars.ContextVar(
    "edrak_usage_context", default=_default
)


def current() -> UsageContext:
    """The context the current call runs under. Safe with no context set."""
    return _current.get()


def use_case_value(use_case: object) -> str | None:
    """Render a ``UseCase`` (or plain string) as the value stored on a record."""
    if use_case is None:
        return None
    value = getattr(use_case, "value", None)
    if isinstance(value, str) and value:
        return value
    text = str(use_case).strip()
    return text or None


@contextmanager
def usage_context(
    *,
    run_id: str | None = None,
    use_case: str | None = None,
    stage: str | None = None,
    operation: str | None = None,
    attempt: int | None = None,
) -> Iterator[UsageContext]:
    """Set the tracking context for the enclosed block."""
    base = _current.get()
    ctx = UsageContext(
        run_id=run_id if run_id is not None else base.run_id,
        use_case=use_case if use_case is not None else base.use_case,
        stage=stage if stage is not None else base.stage,
        operation=operation if operation is not None else base.operation,
        attempt=attempt if attempt is not None else base.attempt,
    )
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
