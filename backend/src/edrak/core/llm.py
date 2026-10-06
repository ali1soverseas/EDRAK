from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .config import settings


def build_chat_model() -> BaseChatModel:
    """Return a chat model configured from environment.

    Uses Ollama Cloud's OpenAI-compatible endpoint by default.
    """
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
    )
