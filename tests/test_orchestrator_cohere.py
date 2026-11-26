import json

import pytest
from edrak.core import llm as llm_module
from edrak.core.config import settings
from edrak.orchestration import planner as planner_module
from edrak.orchestration.planner import LlmPlanner, PlanningError
from langchain_core.messages import AIMessage
from pydantic import BaseModel


class _StructuredReply(BaseModel):
    answer: str


def _plan_json():
    return json.dumps(
        {
            "rationale": "Focus on the market.",
            "assignments": [
                {
                    "worker": "market_intelligence",
                    "goal": "Assess market demand.",
                    "focus": "Market size and growth.",
                }
            ],
        }
    )


def test_shared_factory_builds_nvidia_client_with_scoped_credential(monkeypatch):
    seen = {}

    class FakeChat:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(llm_module, "EdrakChat", FakeChat)
    monkeypatch.setenv("NVIDIA_API_KEY_MARKET", "scoped-token")
    model = llm_module.get_chat_model(
        agent="market",
        model="command-a-test",
        temperature=0.1,
        timeout_seconds=9,
        callbacks=["callback"],
    )

    assert isinstance(model, FakeChat)
    assert seen["model"] == "command-a-test"
    assert seen["api_key"] == "scoped-token"
    assert seen["base_url"] == settings.NVIDIA_BASE_URL
    assert seen["temperature"] == 0.1
    assert seen["timeout"] == 9
    assert seen["max_tokens"] == llm_module.DEFAULT_MAX_TOKENS
    assert seen["max_retries"] == 2
    assert seen["callbacks"] == ["callback"]


def test_shared_factory_builds_openai_client_for_cross_signal(monkeypatch):
    seen = {}

    class FakeChat:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(llm_module, "EdrakChat", FakeChat)
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("OPENAI_API_KEY_CROSS_SIGNAL", "scoped-openai-token")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://nvidia.example/v1")

    model = llm_module.get_chat_model(
        agent="cross_signal",
        model="gpt-4.1",
        provider="openai",
    )

    assert isinstance(model, FakeChat)
    assert seen["model"] == "gpt-4.1"
    assert seen["api_key"] == "scoped-openai-token"
    assert seen["base_url"] == "https://api.openai.com/v1"


def test_openai_override_does_not_use_nvidia_only_key(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_CROSS_SIGNAL",
        "OPENAI_API_KEY_CROSS_SIGNAL_BACKUP",
        "LLM_API_KEY",
        "LLM_API_KEY_CROSS_SIGNAL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-only-token")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not configured"):
        llm_module.get_chat_model(agent="cross_signal", provider="openai")


def test_shared_factory_uses_nvidia_model_from_environment(monkeypatch):
    seen = {}

    class FakeChat:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(llm_module, "EdrakChat", FakeChat)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-token")
    monkeypatch.setenv("NVIDIA_MODEL", "nvidia/test-model")

    llm_module.get_chat_model()

    assert seen["model"] == "nvidia/test-model"


def test_shared_factory_accepts_legacy_llm_configuration(monkeypatch):
    seen = {}

    class FakeChat:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(llm_module, "EdrakChat", FakeChat)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
    monkeypatch.delenv("NVIDIA_TEMPERATURE", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "legacy-token")
    monkeypatch.setenv("LLM_MODEL", "nvidia/legacy-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.4")

    llm_module.get_chat_model()

    assert seen["api_key"] == "legacy-token"
    assert seen["model"] == "nvidia/legacy-model"
    assert seen["base_url"] == "https://legacy.example/v1"
    assert seen["temperature"] == 0.4


def test_planner_calls_nvidia_and_validates_plan(monkeypatch):
    calls = {}

    class FakeModel:
        def invoke(self, messages, **kwargs):
            calls["messages"] = messages
            calls["kwargs"] = kwargs
            return AIMessage(content=_plan_json())

    monkeypatch.setattr(
        planner_module,
        "get_chat_model",
        lambda **kwargs: FakeModel(),
    )

    plan = LlmPlanner()._invoke("Research the market.")

    assert plan.assignments[0].worker.value == "market_intelligence"
    assert calls["messages"][1]["content"] == "Research the market."
    assert calls["kwargs"]["max_tokens"] == llm_module.DEFAULT_MAX_TOKENS


def test_planner_rejects_invalid_nvidia_plan_response(monkeypatch):
    class FakeModel:
        def invoke(self, messages, **kwargs):
            return AIMessage(content="not json")

    monkeypatch.setattr(
        planner_module,
        "get_chat_model",
        lambda **kwargs: FakeModel(),
    )

    with pytest.raises(PlanningError, match="NVIDIA planner response"):
        LlmPlanner()._invoke("Research the market.")


def test_planner_requires_a_nvidia_api_key(monkeypatch):
    for name in (
        "NVIDIA_API_KEY",
        "NVIDIA_API_KEY_ORCHESTRATOR",
        "NVIDIA_API_KEY_ORCHESTRATOR_BACKUP",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", None)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        LlmPlanner()._invoke("Research the market.")


def test_message_text_extracts_text_from_provider_content_blocks():
    message = AIMessage(
        content=[
            {"type": "reasoning", "summary": [{"text": "internal"}]},
            {"type": "text", "text": '{"ok": true}'},
        ]
    )

    assert llm_module.message_text(message) == '{"ok": true}'


def test_shared_structured_helper_builds_json_schema_runnable(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-token")

    runnable = llm_module.get_structured(_StructuredReply, agent="orchestrator")

    assert runnable is not None
