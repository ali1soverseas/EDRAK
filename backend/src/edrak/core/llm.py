"""Shared NVIDIA chat model, credential resolution, and structured-output helpers."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI as EdrakChat

from edrak.core.config import settings

logger = logging.getLogger(__name__)

# Credentials are resolved from os.environ, but they normally live in .env, which
# pydantic reads into its own fields without exporting them. Load it here so the
# per-agent variables are visible. An existing environment variable wins over
# .env, which is the precedence you want for CI and local overrides.
_REPO_ENV = Path(__file__).resolve().parents[4] / ".env"
if _REPO_ENV.is_file():
    load_dotenv(_REPO_ENV, override=False)

# Components that can use independently managed credentials.
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
    It never reads another agent's credentials.

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
    # checks settings, because API keys are Pydantic SecretStr values.
    value = os.getenv(name, "").strip()
    if value:
        return value, name
    configured = getattr(settings, name, None)
    configured = getattr(configured, "get_secret_value", lambda: configured)()
    if configured:
        return str(configured).strip(), name

    # Legacy generic configuration remains a fallback during provider migration.
    if name == "NVIDIA_API_KEY":
        value = os.getenv("LLM_API_KEY", "").strip()
        if value:
            return value, "LLM_API_KEY"
        configured = getattr(settings, "LLM_API_KEY", None)
        configured = getattr(configured, "get_secret_value", lambda: configured)()
        if configured:
            return str(configured).strip(), "LLM_API_KEY"
    return "", name


def nvidia_key(agent: str | None = None) -> str:
    key, variable = provider_key("NVIDIA_API_KEY", agent)
    if not key:
        raise RuntimeError(
            f"{variable} is not configured. Set NVIDIA_API_KEY (or legacy LLM_API_KEY) "
            "in the environment or .env. Create a key at "
            "https://build.nvidia.com/settings/api-keys"
        )
    logger.info("LLM credential resolved from %s", variable)
    return key


DEFAULT_MAX_TOKENS = 16384


def _float_setting(name: str, fallback: float) -> float:
    value = os.getenv(name)
    return float(value) if value else fallback


def get_chat_model(
    model: Optional[str] = None,
    *,
    agent: Optional[str] = None,
    temperature: Optional[float] = None,
    timeout_seconds: Optional[float] = None,
    max_tokens: Optional[int] = None,
    base_url: Optional[str] = None,
    callbacks: Optional[list] = None,
) -> EdrakChat:
    """The chat model every component uses.

    `agent` selects which credential is used.
    """
    provider = os.getenv("LLM_PROVIDER", settings.LLM_PROVIDER).strip().lower()
    if provider != "nvidia":
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER {provider!r}; this build uses NVIDIA."
        )

    extra_kwargs = {}
    if callbacks is not None:
        extra_kwargs["callbacks"] = callbacks
    configured_temperature = _float_setting(
        "NVIDIA_TEMPERATURE",
        _float_setting("LLM_TEMPERATURE", settings.NVIDIA_TEMPERATURE),
    )
    configured_timeout = _float_setting(
        "NVIDIA_TIMEOUT_S", settings.NVIDIA_TIMEOUT_S
    )
    configured_model = (
        os.getenv("NVIDIA_MODEL")
        or os.getenv("LLM_MODEL")
        or settings.NVIDIA_MODEL
        or settings.LLM_MODEL
    )
    configured_base_url = (
        base_url
        or os.getenv("NVIDIA_BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or settings.NVIDIA_BASE_URL
        or settings.LLM_BASE_URL
    )
    return EdrakChat(
        model=model or configured_model,
        base_url=configured_base_url,
        api_key=nvidia_key(agent),
        temperature=configured_temperature if temperature is None else temperature,
        max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
        timeout=timeout_seconds if timeout_seconds is not None else configured_timeout,
        max_retries=2,
        **extra_kwargs,
    )


def message_text(message: object) -> str:
    """Extract text from a LangChain message, including provider content blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_blocks = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if text_blocks:
            return "".join(text_blocks)
    return str(content)


def schema_instruction(schema: type) -> dict:
    """Restate the expected output shape in a user-facing prompt message."""
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

    The schema is also restated in the prompt to keep the requested object
    shape explicit for NVIDIA's OpenAI-compatible endpoint.
    """
    runnable = get_chat_model(
        model, agent=agent, temperature=temperature
    ).with_structured_output(
        schema, method=method, max_tokens=DEFAULT_MAX_TOKENS
    )
    try:
        from edrak.core.observability import get_current_prompt_obj
        prompt_obj = get_current_prompt_obj()
        if prompt_obj is not None:
            return runnable.with_config(metadata={"langfuse_prompt": prompt_obj})
    except Exception:
        pass
    return runnable


def get_json_object(
    system: str,
    user: str,
    *,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    callbacks: Optional[list] = None,
) -> dict:
    """Ask for a JSON object and return it as a dict.

    For callers whose reply shape is large, optional or still evolving, where
    pinning a pydantic schema would be more churn than the guarantee is worth.
    """
    try:
        from edrak.core.observability import get_current_prompt_obj
        prompt_obj = get_current_prompt_obj()
        cfg = {"metadata": {"langfuse_prompt": prompt_obj}} if prompt_obj is not None else None
    except Exception:
        cfg = None
    message = get_chat_model(
        model, agent=agent, temperature=temperature, callbacks=callbacks
    ).invoke(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        config=cfg,
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    text = message_text(message).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"model reply contained no JSON object: {text[:200]!r}")
    return json.loads(match.group(0))
