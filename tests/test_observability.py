"""Tests for EDRAK Langfuse observability, tracing, and prompt versioning."""

from unittest.mock import patch

import pytest
from langchain_core.callbacks.base import BaseCallbackHandler

from edrak.core.config import settings
from edrak.core.llm import get_chat_model
from edrak.core.observability import (
    _safe_format,
    build_observability_config,
    flush_observability,
    get_langfuse_callback_handler,
    get_langfuse_client,
    get_versioned_prompt,
    is_observability_enabled,
)
from edrak.orchestration.prompts import (
    PLANNER_SYSTEM_PROMPT,
    REQUEST_BUILDER_SYSTEM_PROMPT,
    get_planner_prompt,
    get_request_builder_prompt,
)


def test_safe_format_mustache_and_python():
    """Verify both mustache {{key}} and python {key} formatting work seamlessly."""
    template = "Hello {{company}}, assessing market in {market}."
    rendered = _safe_format(template, company="NVIDIA", market="EMEA")
    assert rendered == "Hello NVIDIA, assessing market in EMEA."


def test_observability_disabled_behavior(monkeypatch):
    """When disabled, observability functions return None and don't fail."""
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)

    assert not is_observability_enabled()
    cfg = build_observability_config("test-trace")
    assert cfg["callbacks"] == []
    assert cfg["run_name"] == "test-trace"
    assert "langfuse_trace_name" in cfg["metadata"]


def test_observability_enabled_config_generation(monkeypatch):
    """When enabled and configured, config attaches metadata and callback handler."""
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-mock-1234")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-mock-5678")

    assert is_observability_enabled()
    cfg = build_observability_config(
        "analysis-101",
        session_id="session-xyz",
        user_id="user-corp",
        tags=["nvidia", "orchestration"],
        metadata={"custom_env": "pytest"},
    )

    assert cfg["run_name"] == "analysis-101"
    assert cfg["metadata"]["langfuse_session_id"] == "session-xyz"
    assert cfg["metadata"]["langfuse_user_id"] == "user-corp"
    assert cfg["metadata"]["custom_env"] == "pytest"
    assert "nvidia" in cfg["tags"]
    # Handler should be present in callbacks
    assert len(cfg["callbacks"]) == 1


def test_versioned_prompt_offline_fallback():
    """Verify fallback template works with variable substitution when prompt server is unavailable."""
    rendered = get_versioned_prompt(
        prompt_name="nonexistent-prompt",
        fallback_template="Analyze competitors for {{target}} in market {region}",
        target="Blackwell",
        region="APAC",
    )
    assert rendered == "Analyze competitors for Blackwell in market APAC"


def test_orchestration_versioned_prompts():
    """Verify planner and request builder prompt getters return valid templates."""
    planner_prompt = get_planner_prompt()
    assert "You are the EDRAK control-plane planner" in planner_prompt

    builder_prompt = get_request_builder_prompt()
    assert "You turn a user's business query into a structured EDRAK research request" in builder_prompt


def test_get_chat_model_accepts_callbacks():
    """Verify get_chat_model accepts callbacks parameter without error."""
    class DummyHandler(BaseCallbackHandler):
        pass

    handler = DummyHandler()
    model = get_chat_model(callbacks=[handler])
    assert model is not None
    assert handler in model.callbacks


def test_flush_observability_fail_open():
    """Verify flush_observability never raises errors even when client is unconfigured or fails."""
    flush_observability()
