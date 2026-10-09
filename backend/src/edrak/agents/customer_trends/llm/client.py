"""Ollama Cloud chat model factory and the structured-output and retry helpers."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import httpx
import ollama
from edrak.agents.customer_trends.logging import get_logger
from edrak.agents.customer_trends.settings import Settings, get_settings
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

Role = Literal["planner", "analyst", "writer", "branch"]

_RETRY_ATTEMPTS = 3
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

log = get_logger(__name__)


class LLMConfigError(RuntimeError):
    """The model cannot be built because a setting is missing."""


class StructuredOutputError(RuntimeError):
    """The model did not produce output matching the requested schema."""


def get_chat_model(role: Role, *, settings: Settings | None = None) -> ChatOllama:
    """Build the native Ollama Cloud chat model for a role (never the /v1 shim)."""
    s = settings or get_settings()
    if s.ollama_api_key is None or not s.ollama_api_key.get_secret_value():
        raise LLMConfigError("OLLAMA_API_KEY is not set: add it to .env at the repository root")
    model = s.ollama_model_analysis if role == "analyst" and s.ollama_model_analysis else None
    return ChatOllama(
        model=model or s.ollama_model,
        base_url=s.ollama_base_url,
        temperature=s.ollama_temperature,
        client_kwargs={
            "headers": {"Authorization": f"Bearer {s.ollama_api_key.get_secret_value()}"},
            "timeout": s.ollama_timeout_s,
        },
    )


def is_transient(exc: BaseException) -> bool:
    """Timeouts, connection failures, 429 and 5xx are worth retrying; the rest are not."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    if isinstance(exc, ollama.ResponseError):
        return exc.status_code in _TRANSIENT_STATUS
    return isinstance(exc, httpx.TransportError | ConnectionError | TimeoutError)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def invoke_with_retry[T](call: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
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


def _raw_text(raw: object) -> str:
    content = getattr(raw, "content", raw)
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _parse[S: BaseModel](schema: type[S], parsed: object) -> S:
    if isinstance(parsed, schema):
        return parsed
    return schema.model_validate(parsed)


async def structured_call[S: BaseModel](
    llm: BaseChatModel,
    schema: type[S],
    messages: Sequence[BaseMessage],
    *,
    repair: bool = True,
) -> S:
    """Get a validated `schema` instance, with at most one repair attempt."""
    # function_calling, not json_schema: this method depends on `format=`, which
    # Ollama Cloud ignores, so the model replies in prose, nothing parses, and
    # the repair attempt below fails for the same reason.
    #
    # The schema is delivered through the tool definition, so the messages are
    # passed through untouched. They used to be followed by an instruction to
    # "reply with only a JSON object, no prose", which described the output in
    # the terms the model would otherwise not use and so suppressed the call.
    runnable = llm.with_structured_output(schema, method="function_calling", include_raw=True)
    base = list(messages)
    conversation = list(base)
    attempts = 2 if repair else 1
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        out: Any = await invoke_with_retry(runnable.ainvoke, conversation)
        raw, parsed, error = out["raw"], out["parsed"], out["parsing_error"]
        try:
            if error is not None or parsed is None:
                raise error or OutputParserException("model returned no parsable output")
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
                AIMessage(content=_raw_text(raw)),
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
