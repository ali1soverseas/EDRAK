import asyncio

from edrak.agents.customer_trends.llm.client import is_transient, structured_call
from edrak.agents.customer_trends.llm.fake import ScriptedChatModel
from edrak.agents.customer_trends.settings import Settings
from langchain_core.messages import HumanMessage
from pydantic import BaseModel


class _Answer(BaseModel):
    value: str


def test_structured_call_repairs_schema_parse_failure():
    model = ScriptedChatModel(
        responses=[
            {"value": 7},
            {"value": "repaired"},
        ]
    )

    answer = asyncio.run(
        structured_call(model, _Answer, [HumanMessage(content="Answer.")])
    )

    assert answer.value == "repaired"
    assert len(model.calls) == 2
    assert "previous output was invalid" in model.calls[1][-1].content


def test_customer_trends_retries_provider_rate_limit_errors():
    class RateLimitError(RuntimeError):
        status_code = 429

    assert is_transient(RateLimitError("rate limited"))


def test_customer_trends_settings_accept_legacy_llm_model_alias(monkeypatch):
    monkeypatch.delenv("NVIDIA_MODEL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "nvidia/legacy-model")

    assert Settings(_env_file=None).nvidia_model == "nvidia/legacy-model"
