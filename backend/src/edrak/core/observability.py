"""Observability, tracing, and prompt management for EDRAK via Langfuse.

Provides centralized tracing, execution graph visualization, token accounting,
and prompt versioning with zero-overhead offline fallbacks.
"""

from __future__ import annotations

import contextvars
import logging
import os
from typing import Any, Dict, List, Optional

from edrak.core.config import settings

logger = logging.getLogger(__name__)

_CLIENT: Optional[Any] = None
_CLIENT_INITIALIZED: bool = False
_CURRENT_PROMPT_OBJ: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "_CURRENT_PROMPT_OBJ", default=None
)


def get_current_prompt_obj() -> Optional[Any]:
    """Return the active Langfuse prompt client object in the current context."""
    return _CURRENT_PROMPT_OBJ.get()


def set_current_prompt_obj(prompt_obj: Optional[Any]) -> None:
    """Explicitly set the active Langfuse prompt client object in the current context."""
    _CURRENT_PROMPT_OBJ.set(prompt_obj)


def is_observability_enabled() -> bool:
    """Return True if Langfuse observability is enabled and credentials are set."""
    if not getattr(settings, "LANGFUSE_ENABLED", False):
        return False
    pub_key = getattr(settings, "LANGFUSE_PUBLIC_KEY", None) or os.getenv("LANGFUSE_PUBLIC_KEY")
    sec_key = getattr(settings, "LANGFUSE_SECRET_KEY", None) or os.getenv("LANGFUSE_SECRET_KEY")
    return bool(pub_key and sec_key)


def get_langfuse_client() -> Optional[Any]:
    """Get the singleton Langfuse client instance, or None if disabled/unreachable."""
    global _CLIENT, _CLIENT_INITIALIZED
    if _CLIENT_INITIALIZED:
        return _CLIENT

    if not is_observability_enabled():
        _CLIENT = None
        _CLIENT_INITIALIZED = True
        return None

    try:
        from langfuse import Langfuse

        pub_key = getattr(settings, "LANGFUSE_PUBLIC_KEY", None) or os.getenv("LANGFUSE_PUBLIC_KEY")
        sec_key = getattr(settings, "LANGFUSE_SECRET_KEY", None) or os.getenv("LANGFUSE_SECRET_KEY")
        host = getattr(settings, "LANGFUSE_HOST", None) or os.getenv("LANGFUSE_HOST", "http://localhost:3000")
        release = getattr(settings, "LANGFUSE_RELEASE", "edrak-2.0.0")

        _CLIENT = Langfuse(
            public_key=pub_key,
            secret_key=sec_key,
            host=host,
            release=release,
        )
        logger.info("Langfuse observability client initialized for host: %s", host)
    except Exception as exc:
        logger.warning("Failed to initialize Langfuse client; continuing without tracing: %s", exc)
        _CLIENT = None

    _CLIENT_INITIALIZED = True
    return _CLIENT


def get_langfuse_callback_handler(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    trace_name: Optional[str] = None,
) -> Optional[Any]:
    """Get a LangChain CallbackHandler for tracing LLM and LangGraph runs."""
    if not is_observability_enabled():
        return None

    client = get_langfuse_client()
    if client is None:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        pub_key = getattr(settings, "LANGFUSE_PUBLIC_KEY", None) or os.getenv("LANGFUSE_PUBLIC_KEY")
        handler = CallbackHandler(public_key=pub_key)
        return handler
    except Exception as exc:
        logger.warning("Failed to create Langfuse CallbackHandler: %s", exc)
        return None


def build_observability_config(
    trace_name: str,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct a LangChain/LangGraph config dict attaching callbacks and trace metadata."""
    handler = get_langfuse_callback_handler(
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        trace_name=trace_name,
    )
    callbacks = [handler] if handler is not None else []
    all_tags = list(tags) if tags else []

    meta: Dict[str, Any] = {
        "langfuse_trace_name": trace_name,
    }
    if session_id:
        meta["langfuse_session_id"] = str(session_id)
    if user_id:
        meta["langfuse_user_id"] = str(user_id)
    if all_tags:
        meta["langfuse_tags"] = all_tags
    if metadata:
        meta.update(metadata)

    return {
        "callbacks": callbacks,
        "metadata": meta,
        "tags": all_tags,
        "run_name": trace_name,
    }


def _safe_format(template: str, **kwargs: Any) -> str:
    """Format template substituting {{key}} or {key} with fallback to key name."""
    result = template
    # Replace mustache style {{var}}
    for k, v in kwargs.items():
        result = result.replace(f"{{{{{k}}}}}", str(v))
    # Replace python style {var} if left
    for k, v in kwargs.items():
        result = result.replace(f"{{{k}}}", str(v))
    return result


def get_versioned_prompt(
    prompt_name: str,
    fallback_template: str,
    *,
    label: str = "production",
    **kwargs: Any,
) -> str:
    """Fetch prompt from Langfuse with offline fallback to Python code templates.

    Also caches the fetched Langfuse prompt object in contextvars so subsequent
    LLM generations automatically link to this prompt in the Langfuse Prompts catalog.

    Args:
        prompt_name: Unique prompt identifier in Langfuse prompt catalog.
        fallback_template: Hardcoded prompt template in code if offline or unconfigured.
        label: Prompt label (default 'production').
        **kwargs: Variables to compile into the prompt template.

    Returns:
        The formatted prompt text.
    """
    client = get_langfuse_client()
    if client is not None:
        try:
            prompt_obj = client.get_prompt(
                name=prompt_name,
                label=label,
                fallback=fallback_template,
                fetch_timeout_seconds=2,
            )
            # Register active prompt object for linkage in Langfuse dashboard
            _CURRENT_PROMPT_OBJ.set(prompt_obj)

            if hasattr(prompt_obj, "compile"):
                compiled = prompt_obj.compile(**kwargs)
                return _safe_format(compiled, **kwargs)
        except Exception as exc:
            logger.debug("Langfuse prompt fetch for '%s' failed, using fallback: %s", prompt_name, exc)

    return _safe_format(fallback_template, **kwargs)


def flush_observability() -> None:
    """Flush any queued trace events to the Langfuse server."""
    client = get_langfuse_client()
    if client is not None:
        try:
            client.flush()
        except Exception as exc:
            logger.debug("Failed to flush Langfuse events: %s", exc)
