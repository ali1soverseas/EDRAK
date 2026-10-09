"""LLM access for EDRAK.

New components should use `get_chat_model` or `get_structured`. They talk to
the native Ollama client rather than posting to an OpenAI-compatible shim by
hand, because Ollama documents that shim as experimental and subject to
breaking changes.

`LLMClient` is the older hand-rolled httpx path. It stays until its remaining
callers are moved, then it is removed.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from langchain_ollama import ChatOllama

from edrak.core.config import settings

logger = logging.getLogger(__name__)

# The native Ollama API lives at the bare host. The /v1 path is the
# OpenAI-compatible shim, which ChatOllama does not speak.
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
    """The one chat model every component uses.

    ChatOllama has no api_key argument: passing one is silently discarded as a
    pydantic extra and the request goes out unauthenticated, which surfaces as
    a 401 rather than a configuration error. Auth therefore has to go through
    client_kwargs.
    """
    return ChatOllama(
        model=model or settings.OLLAMA_MODEL,
        base_url=base_url or NATIVE_BASE_URL,
        temperature=settings.OLLAMA_TEMPERATURE if temperature is None else temperature,
        num_predict=2048,
        client_kwargs={"headers": {"Authorization": f"Bearer {ollama_key()}"}},
    )


def get_structured(schema, model: Optional[str] = None, *, temperature: Optional[float] = None):
    """A model bound to `schema` for structured output.

    `function_calling` is the only method that works against Ollama Cloud.
    `json_schema` and `json_mode` both rely on `format=`, which Cloud ignores,
    so the model answers in prose and parsing fails.
    """
    return get_chat_model(model, temperature=temperature).with_structured_output(
        schema, method="function_calling"
    )


class LLMClient:
    """Legacy hand-rolled client. Superseded by `get_chat_model`."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or settings.OLLAMA_BASE_URL or "http://localhost:11434/v1").rstrip("/")
        self.api_key = api_key or settings.OLLAMA_API_KEY or "ollama"
        self.model = model or settings.OLLAMA_MODEL or "gpt-oss:120b"
        self.temperature = temperature if temperature is not None else settings.OLLAMA_TEMPERATURE
        self.timeout = timeout

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        json_mode: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Calls the chat completions endpoint and returns the message content."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key and self.api_key != "ollama":
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = max_tokens

        logger.info("Calling LLM (%s) at %s...", self.model, url)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return content
        except Exception as e:
            logger.error("LLM API request failed (%s): %s", url, e)
            raise

    def chat_structured(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calls chat completions in JSON mode and parses the JSON response."""
        content = self.chat_completion(messages=messages, json_mode=True, temperature=temperature)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Attempt to extract JSON from code block
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            # Attempt to find first { to last }
            first_brace = content.find("{")
            last_brace = content.rfind("}")
            if first_brace != -1 and last_brace != -1:
                return json.loads(content[first_brace : last_brace + 1])
            raise ValueError(f"Could not parse valid JSON from LLM response: {content[:200]}...")


_global_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Returns the legacy client. Migrate callers to `get_chat_model`."""
    global _global_llm_client
    if _global_llm_client is None:
        _global_llm_client = LLMClient()
    return _global_llm_client
