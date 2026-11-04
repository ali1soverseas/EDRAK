"""LLM access for EDRAK.

One model, one client. Every component builds its chat model here, so there is
a single place where the provider, endpoint, credential and output method are
decided.

Two things about Ollama are worth knowing before changing anything here.

Auth does not go through an argument. ChatOllama has no api_key field;
passing one is silently discarded as a pydantic extra and the request goes out
unauthenticated, which surfaces as a 401 rather than a configuration error.

Structured output goes through json_schema plus the schema restated in the
prompt, and both halves are load-bearing. Measured on gpt-oss:120b against the
schemas this project actually sends, first attempt, repair off:

  json_schema + schema instruction   works on flat and nested schemas alike
  function_calling                  0/5 on every nested-list schema tried

Tool calling is not merely worse here, it returns nothing at all for a schema
whose top-level field is a list of objects, which is most of them. And
`json_schema` alone is not sufficient: without the instruction the model
answers in prose or picks its own shape, such as returning the bare array
instead of the object wrapping it.

So `get_structured` binds json_schema and `schema_instruction` puts the schema
in the prompt. Do not drop either, and do not copy a method from another
worker without measuring it on that worker's own schemas.
"""

import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from edrak.core.config import settings

logger = logging.getLogger(__name__)

# Credentials are resolved from os.environ, but they normally live in .env, which
# pydantic reads into its own fields without exporting them. Load it here so the
# per-agent variables are visible. An existing environment variable wins over
# .env, which is the precedence you want for CI and local overrides.
_REPO_ENV = Path(__file__).resolve().parents[4] / ".env"
if _REPO_ENV.is_file():
    load_dotenv(_REPO_ENV, override=False)

# The native Ollama API lives at the bare host. The /v1 path is an
# OpenAI-compatible shim that Ollama documents as experimental, and ChatOllama
# does not speak it.
NATIVE_BASE_URL = "https://ollama.com"

# Every component that can call a model. Each maps to its own credential so one
# agent exhausting its quota cannot starve the others.
AGENTS = (
    "orchestrator",
    "competitor",
    "market",
    "internal",
    "cross_signal",
    "customer_trends",
    "metrics",
)


def provider_key(name: str, agent: str | None = None, *, backup: bool = True) -> tuple[str, str]:
    """Resolve a credential and report which variable supplied it.

    Lookup order is the agent's primary, then the agent's backup, then the
    shared variable. A scoped variable therefore always beats the shared one,
    and the backup is only reached when the primary is absent or empty.

    An agent only ever reads its own scoped variables and the shared fallback.
    It never reads another agent's, which is what keeps one agent's exhausted
    quota from affecting the rest.

    Returns the value and the name of the variable it came from, so a caller can
    log which credential was spent without ever logging the value.
    """
    if agent:
        scoped = f"{name}_{agent.upper()}"
        candidates = [scoped, f"{scoped}_BACKUP"] if backup else [scoped]
        for candidate in candidates:
            value = os.getenv(candidate, "").strip()
            if value:
                return value, candidate

    # Scoped variables only exist in the environment. The shared fallback also
    # checks settings, because a credential renamed with an alias (LLM_API_KEY
    # for OLLAMA_API_KEY) is resolved there and never reaches os.environ.
    value = os.getenv(name, "").strip()
    if value:
        return value, name
    configured = getattr(settings, name, None)
    configured = getattr(configured, "get_secret_value", lambda: configured)()
    configured = str(configured or "").strip()
    if configured:
        return configured, name
    # A shared backup is the last resort: it is reached only when an agent's own
    # key, its scoped backup and the shared primary are all missing.
    backup_value = os.getenv(f"{name}_BACKUP", "").strip()
    if backup_value:
        return backup_value, f"{name}_BACKUP"
    return "", name


def ollama_key(agent: str | None = None) -> str:
    key, variable = provider_key("OLLAMA_API_KEY", agent)
    if not key:
        raise RuntimeError(
            f"{variable} is not configured. Set it in the environment or .env."
        )
    logger.info("LLM credential resolved from %s", variable)
    return key


# Trial provider: Groq's OpenAI-compatible endpoint. LLM_PROVIDER=groq routes
# every component here for as long as the trial lasts; removing the variable
# returns everything to Ollama with no code change.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def active_provider() -> str:
    """The configured provider: ``groq`` for the trial, ``ollama`` by default."""
    return os.getenv("LLM_PROVIDER", "").strip().lower() or "ollama"


def groq_key(agent: str | None = None) -> str:
    """The Groq credential, resolved through the same per-agent chain."""
    key, variable = provider_key("GROQ_API_KEY", agent)
    if not key:
        raise RuntimeError(
            f"{variable} is not configured. Set it in the environment or .env."
        )
    logger.info("LLM credential resolved from %s", variable)
    return key


# A structured reply over a real evidence pool runs long: internal synthesis
# reached 2048 on eight items and was cut off mid-string, which surfaces as a
# JSON parse error rather than as a truncation. This leaves room for a reply
# several times that size. It is a ceiling, not a target; temperature and the
# schema decide the actual length.
DEFAULT_NUM_PREDICT = 16384


# --------------------------------------------------------------------------- #
# Groq pacing
# --------------------------------------------------------------------------- #
# Groq's free tier meters each account at 8k tokens per minute, and that 8k is
# split: 7k input and 1k output. An input burst still fails with 429 when a
# worker sends several calls at once, which is what put 120 metric calls on one
# account in the qwen trial. The bucket below spends each account's minute on
# purpose: it reserves the estimated prompt plus an output allowance before the
# request leaves, and serialises callers sharing a credential.
#
# The output half has a ceiling of its own: a request whose *expected* reply
# does not fit the remaining output minute is refused outright ("Request too
# large ... on output tokens per minute"), no matter how small the input. That
# is why the market worker states a reply ceiling per call (see
# call_structured) and why the reserve here is not merely a fudge factor.
#
# It is a callback, not a wrapper, so it sits on the model that is actually
# invoked: plain calls, structured-output runs and retries all pass through it.

_GROQ_WINDOW_S = 60.0
# Kept under 7k so an input-heavy minute cannot cross the input half on its own.
_GROQ_TOKEN_BUDGET = 7_200
_GROQ_REQUEST_BUDGET = 24
_GROQ_OUTPUT_RESERVE = 600

_buckets: dict[str, "_GroqBucket"] = {}
_buckets_lock = threading.Lock()


class _GroqBucket:
    """A rolling one-minute token budget for one Groq credential."""

    def __init__(self) -> None:
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= _GROQ_WINDOW_S:
            self._events.popleft()

    def acquire(self, tokens: int) -> None:
        if tokens > _GROQ_TOKEN_BUDGET:
            raise RuntimeError(
                f"Groq prompt needs about {tokens} tokens, over the "
                f"{_GROQ_TOKEN_BUDGET}-token minute; split it before sending"
            )
        while True:
            now = time.monotonic()
            with self._lock:
                self._prune(now)
                used = sum(t for _, t in self._events)
                full = used + tokens > _GROQ_TOKEN_BUDGET
                too_many = len(self._events) >= _GROQ_REQUEST_BUDGET
                if not full and not too_many:
                    self._events.append((now, tokens))
                    return
                wait = (
                    _GROQ_WINDOW_S - (now - self._events[0][0])
                    if self._events
                    else 0.5
                )
            time.sleep(max(0.2, min(wait + 0.05, 5.0)))


def _bucket_for(credential: str) -> "_GroqBucket":
    with _buckets_lock:
        bucket = _buckets.get(credential)
        if bucket is None:
            bucket = _GroqBucket()
            _buckets[credential] = bucket
        return bucket


def _message_chars(messages) -> int:
    total = 0
    for batch in messages:
        for message in batch:
            content = getattr(message, "content", message)
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total += len(str(part.get("text") or part))
                    else:
                        total += len(str(part))
            else:
                total += len(str(content))
    return total


def estimate_prompt_tokens(messages) -> int:
    """Rough token count for a prompt batch: JSON/XML runs near 3.5 chars/token."""
    return max(1, int(_message_chars(messages) / 3.5))


class _GroqPaceCallback(BaseCallbackHandler):
    """Wait for this credential's budget before a chat request is sent."""

    def __init__(self, credential: str) -> None:
        self.credential = credential

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        needed = estimate_prompt_tokens(messages) + _GROQ_OUTPUT_RESERVE
        logger.debug(
            "groq pacing %s: reserving %d tokens", self.credential, needed
        )
        _bucket_for(self.credential).acquire(needed)

    def on_llm_error(self, error: BaseException, **kwargs) -> None:
        # The usage record keeps only the exception type. When a 429 arrives
        # anyway, its body names the limit that was hit and the wait Groq
        # expects, which is what the pacing settings need to answer to.
        logger.warning(
            "groq call failed on %s: %s", self.credential, str(error)[:400]
        )


def get_chat_model(
    model: Optional[str] = None,
    *,
    agent: Optional[str] = None,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
    num_predict: Optional[int] = None,
) -> BaseChatModel:
    """The chat model every component uses.

    `agent` selects which credential is used. Passing it is what keeps one
    agent's quota separate from another's.

    The usage callback is attached here so every model request is recorded,
    whatever the caller does with the model afterwards: a plain invoke, a
    structured-output run and a retry all pass through the same model object and
    therefore through the same handler.

    The provider comes from LLM_PROVIDER: ``groq`` for the trial, ``ollama``
    otherwise and by default. The Groq branch speaks Groq's OpenAI-compatible
    endpoint through ChatOpenAI, resolves GROQ_API_KEY through the same
    per-agent chain, and records through the same callback. `max_retries=0`
    keeps SDK-level retries from silently absorbing failures: a failed call
    reaches the tracker as one failure instead of vanishing inside the client.
    """
    from edrak.usage.store import enabled as _usage_enabled
    from edrak.usage.tracker import usage_callback

    callbacks = [usage_callback()] if _usage_enabled() else None

    if active_provider() == "groq":
        from langchain_openai import ChatOpenAI

        key, variable = provider_key("GROQ_API_KEY", agent)
        if not key:
            raise RuntimeError(
                f"{variable} is not configured. Set it in the environment or .env."
            )
        logger.info("LLM credential resolved from %s", variable)

        # Pace before recording: the usage callback's latency should measure
        # the request, not the wait this trial deliberately inserts.
        paced = [_GroqPaceCallback(variable)]
        if callbacks:
            paced.extend(callbacks)

        return ChatOpenAI(
            model=model or os.getenv("GROQ_MODEL", "").strip() or settings.OLLAMA_MODEL,
            base_url=base_url or GROQ_BASE_URL,
            api_key=key,
            temperature=settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
            max_completion_tokens=num_predict or DEFAULT_NUM_PREDICT,
            max_retries=0,
            callbacks=paced,
        )

    return ChatOllama(
        model=model or settings.OLLAMA_MODEL,
        base_url=base_url or NATIVE_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
        num_predict=num_predict or DEFAULT_NUM_PREDICT,
        client_kwargs={"headers": {"Authorization": f"Bearer {ollama_key(agent)}"}},
        callbacks=callbacks,
    )


def schema_instruction(schema: type) -> dict:
    """Restate `schema` in the prompt as the shape to answer with.

    Every structured call needs this. json_schema alone does not hard-constrain
    the decoder, so without it the model answers in prose or picks its own
    shape. Measured on the cross-signal schema: 0/5 without this message, 5/5
    with it.
    """
    return {
        "role": "user",
        "content": (
            "Reply with only a JSON object that validates against this JSON schema, "
            "with no prose and no code fences:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        ),
    }


def get_structured(
    schema,
    model: Optional[str] = None,
    *,
    agent: Optional[str] = None,
    temperature: Optional[float] = None,
    method: str = "json_schema",
    num_predict: Optional[int] = None,
):
    """A model bound to `schema` for structured output.

    Defaults to `json_schema`, with the schema restated in the prompt by
    `schema_instruction`. Measured, not preferred: `function_calling`, the
    previous default, returned no structured output at all on every
    nested-list schema tried here. Pass `method` only with a measurement
    behind it, and never without the instruction.

    json_schema steers the reply rather than hard-constraining the decoder, so
    still validate: a prose reply or a wrong top-level shape does slip
    through when the instruction is omitted.

    `num_predict` is the reply ceiling handed to the provider. On a tier that
    meters output tokens per minute, a request whose expected reply is larger
    than the whole minute is refused outright, so call sites with a small
    reply set the ceiling explicitly.
    """
    return get_chat_model(
        model, agent=agent, temperature=temperature, num_predict=num_predict
    ).with_structured_output(schema, method=method)


def get_json_object(
    system: str,
    user: str,
    *,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> dict:
    """Ask for a JSON object and return it as a dict.

    For callers whose reply shape is large, optional or still evolving, where
    pinning a pydantic schema would be more churn than the guarantee is worth.
    """
    message = get_chat_model(
        model, agent=agent, temperature=temperature
    ).invoke([{"role": "system", "content": system}, {"role": "user", "content": user}])
    text = str(message.content).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"model reply contained no JSON object: {text[:200]!r}")
    return json.loads(match.group(0))
