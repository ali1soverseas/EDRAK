"""LLM access for EDRAK.

One model, one client. Every component builds its chat model here, so there is
a single place where the provider, endpoint, credential and output method are
decided.

Two things about Ollama are worth knowing before changing anything here.

Auth does not go through an argument. ChatOllama has no api_key field;
passing one is silently discarded as a pydantic extra and the request goes out
unauthenticated, which surfaces as a 401 rather than a configuration error.

Structured output goes through tool calling. Ollama Cloud ignores the
`format=` parameter, so `json_schema` and `json_mode` do not constrain the
reply: the model answers in prose and nothing parses. Prompts must therefore
not describe the expected JSON shape either, since an instruction to "reply
with only JSON" suppresses the tool call just as reliably.
"""

import json
import logging
import re
from typing import Optional

from langchain_ollama import ChatOllama

from edrak.core.config import settings

logger = logging.getLogger(__name__)

# The native Ollama API lives at the bare host. The /v1 path is an
# OpenAI-compatible shim that Ollama documents as experimental, and ChatOllama
# does not speak it.
NATIVE_BASE_URL = "https://ollama.com"


def ollama_key() -> str:
    key = settings.OLLAMA_API_KEY
    if not key or not str(key).strip():
        raise RuntimeError(
            "OLLAMA_API_KEY is not configured. Set it in the environment or .env."
        )
    return str(key)


def get_chat_model(
    model: Optional[str] = None,
    *,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
) -> ChatOllama:
    """The chat model every component uses."""
    return ChatOllama(
        model=model or settings.OLLAMA_MODEL,
        base_url=base_url or NATIVE_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
        num_predict=2048,
        client_kwargs={"headers": {"Authorization": f"Bearer {ollama_key()}"}},
    )


def get_structured(
    schema,
    model: Optional[str] = None,
    *,
    temperature: Optional[float] = None,
    method: str = "function_calling",
):
    """A model bound to `schema` for structured output.

    Defaults to `function_calling`, the only method that constrains output
    against Ollama Cloud. `method="json_mode"` is the documented exception for
    the two call sites whose replies are lists of objects, which this model
    declines to emit as tool calls. json_mode parses rather than enforces, so
    validate anything that matters.
    """
    return get_chat_model(model, temperature=temperature).with_structured_output(
        schema, method=method
    )


def get_json_object(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> dict:
    """Ask for a JSON object and return it as a dict.

    For callers whose reply shape is large, optional or still evolving, where
    pinning a pydantic schema would be more churn than the guarantee is worth.
    """
    message = get_chat_model(model, temperature=temperature).invoke(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )
    text = str(message.content).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"model reply contained no JSON object: {text[:200]!r}")
    return json.loads(match.group(0))
