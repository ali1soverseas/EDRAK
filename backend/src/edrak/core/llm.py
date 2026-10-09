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
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
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
    return str(configured or "").strip(), name


def ollama_key(agent: str | None = None) -> str:
    key, variable = provider_key("OLLAMA_API_KEY", agent)
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


def get_chat_model(
    model: Optional[str] = None,
    *,
    agent: Optional[str] = None,
    temperature: Optional[float] = None,
    base_url: Optional[str] = None,
    num_predict: Optional[int] = None,
) -> ChatOllama:
    """The chat model every component uses.

    `agent` selects which credential is used. Passing it is what keeps one
    agent's quota separate from another's.
    """
    return ChatOllama(
        model=model or settings.OLLAMA_MODEL,
        base_url=base_url or NATIVE_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
        num_predict=num_predict or DEFAULT_NUM_PREDICT,
        client_kwargs={"headers": {"Authorization": f"Bearer {ollama_key(agent)}"}},
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
    """
    return get_chat_model(
        model, agent=agent, temperature=temperature
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
