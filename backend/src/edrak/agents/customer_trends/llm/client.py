"""NVIDIA chat model factory and the structured-output and retry helpers."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, TypeVar

import httpx
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.settings import Settings, get_settings
from edrak.core.llm import (
    DEFAULT_MAX_TOKENS,
    schema_instruction,
)
from edrak.core.llm import (
    get_chat_model as get_shared_chat_model,
)
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

T = TypeVar("T")
S = TypeVar("S", bound=BaseModel)

Role = Literal["planner", "analyst", "writer", "branch"]

_RETRY_ATTEMPTS = 3
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

log = get_logger(__name__)


class LLMConfigError(RuntimeError):
    """The model cannot be built because a setting is missing."""


class StructuredOutputError(RuntimeError):
    """The model did not produce output matching the requested schema."""


def get_chat_model(role: Role, *, settings: Settings | None = None) -> BaseChatModel:
    """Build the shared NVIDIA chat model with customer-trends role settings."""
    s = settings or get_settings()
    model = s.nvidia_model_analysis if role == "analyst" and s.nvidia_model_analysis else s.nvidia_model
    try:
        return get_shared_chat_model(
            model=model,
            agent="customer_trends",
            temperature=s.nvidia_temperature,
            timeout_seconds=s.nvidia_timeout_s,
        )
    except RuntimeError as exc:
        raise LLMConfigError(str(exc)) from exc


def is_transient(exc: BaseException) -> bool:
    """Timeouts, connection failures, 429 and 5xx are worth retrying; the rest are not."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    if getattr(exc, "status_code", None) in _TRANSIENT_STATUS:
        return True
    return isinstance(exc, httpx.TransportError | ConnectionError | TimeoutError)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def invoke_with_retry(call: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
    """Await `call` with up to three attempts on transient errors only."""
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(_RETRY_ATTEMPTS),
        wait=wait_random_exponential(multiplier=0.5, max=8),
        sleep=_sleep,
        reraise=True,
    ):
        with attempt:
            return await call(*args, **kwargs)
    raise AssertionError("unreachable: AsyncRetrying always yields an attempt")


def _schema_instruction(schema: type[BaseModel]) -> HumanMessage:
    """The shared helper from edrak.core.llm, as a message this worker can append."""
    return HumanMessage(content=schema_instruction(schema)["content"])


def _parse(schema: type[S], parsed: object) -> S:
    if isinstance(parsed, schema):
        return parsed
    return schema.model_validate(parsed)


async def structured_call(
    llm: BaseChatModel,
    schema: type[S],
    messages: Sequence[BaseMessage],
    *,
    repair: bool = True,
) -> S:
    """Get a validated `schema` instance, with at most one repair attempt."""
    runnable = llm.with_structured_output(
        schema, method="json_schema", max_tokens=DEFAULT_MAX_TOKENS
    )
    base = [*messages, _schema_instruction(schema)]
    conversation = list(base)
    attempts = 2 if repair else 1
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            parsed: Any = await invoke_with_retry(runnable.ainvoke, conversation)
            result = _parse(schema, parsed)
        except (ValidationError, OutputParserException) as exc:
            error = exc
            log.warning(
                "structured_call_invalid",
                schema=schema.__name__,
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            conversation = [
                *base,
                HumanMessage(
                    content=(
                        f"Your previous output was invalid: {exc}\n"
                        "Reply again with only the corrected output matching the schema."
                    )
                ),
            ]
            continue
        log.info("structured_call_ok", schema=schema.__name__, attempt=attempt)
        return result
    raise StructuredOutputError(
        f"{schema.__name__}: no valid output after {attempts} attempt(s): {error}"
    ) from error
