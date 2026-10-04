import httpx
import ollama
import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

from edrak.agents.customer_trends.llm import client
from edrak.agents.customer_trends.llm.client import (
    LLMConfigError,
    StructuredOutputError,
    get_chat_model,
    invoke_with_retry,
    is_transient,
    structured_call,
)
from edrak.agents.customer_trends.llm.fake import ScriptedChatModel, ScriptExhaustedError
from edrak.agents.customer_trends.settings import Settings


class Capital(BaseModel):
    country: str
    capital: str


def make_settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]  # kwargs are test overrides


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(client, "_sleep", instant)


def test_chat_model_uses_native_endpoint_and_bearer_header() -> None:
    s = make_settings(ollama_api_key="key-123", ollama_timeout_s=30, ollama_temperature=0.4)
    model = get_chat_model("planner", settings=s)
    assert model.base_url == "https://ollama.com"
    assert "/v1" not in (model.base_url or "")
    assert model.client_kwargs["headers"] == {"Authorization": "Bearer key-123"}
    assert model.client_kwargs["timeout"] == 30
    assert model.temperature == 0.4
    assert model.model == "gpt-oss:120b"


def test_analyst_role_prefers_analysis_model_when_set() -> None:
    s = make_settings(ollama_api_key="k", ollama_model="base", ollama_model_analysis="deep")
    assert get_chat_model("analyst", settings=s).model == "deep"
    for role in ("planner", "writer", "branch"):
        assert get_chat_model(role, settings=s).model == "base"  # type: ignore[arg-type]


def test_analyst_role_falls_back_to_base_model() -> None:
    s = make_settings(ollama_api_key="k", ollama_model="base")
    assert get_chat_model("analyst", settings=s).model == "base"


def test_missing_key_raises_only_when_a_model_is_requested() -> None:
    s = make_settings()
    with pytest.raises(LLMConfigError, match="OLLAMA_API_KEY"):
        get_chat_model("branch", settings=s)


async def test_structured_call_success() -> None:
    llm = ScriptedChatModel(responses=[{"country": "France", "capital": "Paris"}])
    result = await structured_call(llm, Capital, [HumanMessage(content="capital of France")])
    assert result == Capital(country="France", capital="Paris")
    assert len(llm.calls) == 1


async def test_structured_call_repairs_once_and_shows_the_error() -> None:
    llm = ScriptedChatModel(
        responses=[{"country": "France"}, {"country": "France", "capital": "Paris"}]
    )
    result = await structured_call(llm, Capital, [HumanMessage(content="capital of France")])
    assert result.capital == "Paris"
    repair_prompt = llm.calls[1]
    assert repair_prompt[0].content == "capital of France"
    assert '"country": "France"' in str(repair_prompt[1].content)
    assert "capital" in str(repair_prompt[2].content)
    assert "invalid" in str(repair_prompt[2].content)


async def test_structured_call_fails_after_one_repair() -> None:
    llm = ScriptedChatModel(responses=[{"country": "France"}, {"capital": "Paris"}, {"x": 1}])
    with pytest.raises(StructuredOutputError, match="2 attempt"):
        await structured_call(llm, Capital, [HumanMessage(content="q")])
    assert len(llm.calls) == 2


async def test_structured_call_without_repair_makes_one_attempt() -> None:
    llm = ScriptedChatModel(responses=[{"country": "France"}, {"country": "F", "capital": "P"}])
    with pytest.raises(StructuredOutputError, match="1 attempt"):
        await structured_call(llm, Capital, [HumanMessage(content="q")], repair=False)
    assert len(llm.calls) == 1


async def test_structured_call_lets_unrelated_errors_through() -> None:
    llm = ScriptedChatModel(responses=[])
    with pytest.raises(ScriptExhaustedError):
        await structured_call(llm, Capital, [HumanMessage(content="q")])


def test_transient_classification() -> None:
    request = httpx.Request("POST", "https://ollama.com/api/chat")
    assert is_transient(httpx.ConnectTimeout("t"))
    assert is_transient(httpx.ConnectError("c"))
    assert is_transient(TimeoutError())
    assert is_transient(ConnectionError())
    assert is_transient(ollama.ResponseError("busy", 503))
    assert is_transient(ollama.ResponseError("slow down", 429))
    assert not is_transient(ollama.ResponseError("bad request", 400))
    assert is_transient(
        httpx.HTTPStatusError("e", request=request, response=httpx.Response(502, request=request))
    )
    assert not is_transient(
        httpx.HTTPStatusError("e", request=request, response=httpx.Response(401, request=request))
    )
    assert not is_transient(ValueError("v"))


class Flaky:
    def __init__(self, failures: list[BaseException]) -> None:
        self.failures = failures
        self.calls = 0

    async def __call__(self, value: str) -> str:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return value.upper()


async def test_retries_transient_errors_until_success() -> None:
    flaky = Flaky([httpx.ConnectTimeout("t"), ollama.ResponseError("busy", 503)])
    assert await invoke_with_retry(flaky, "ok") == "OK"
    assert flaky.calls == 3


async def test_gives_up_after_three_attempts_and_reraises() -> None:
    flaky = Flaky([httpx.ReadTimeout("t")] * 5)
    with pytest.raises(httpx.ReadTimeout):
        await invoke_with_retry(flaky, "ok")
    assert flaky.calls == 3


async def test_does_not_retry_validation_errors() -> None:
    try:
        Capital.model_validate({})
    except ValidationError as exc:
        error = exc
    flaky = Flaky([error])
    with pytest.raises(ValidationError):
        await invoke_with_retry(flaky, "ok")
    assert flaky.calls == 1


async def test_does_not_retry_client_errors() -> None:
    flaky = Flaky([ollama.ResponseError("unauthorized", 401)])
    with pytest.raises(ollama.ResponseError):
        await invoke_with_retry(flaky, "ok")
    assert flaky.calls == 1
