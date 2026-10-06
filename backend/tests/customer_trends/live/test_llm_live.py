"""One real call to the model for each thing the worker needs from it."""

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from edrak.agents.customer_trends.llm.client import get_chat_model, structured_call
from tests.customer_trends.live.conftest import live_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not live_settings().has_key("ollama_api_key"), reason="OLLAMA_API_KEY is not set"
    ),
]


class Answer(BaseModel):
    city: str
    country: str


async def test_a_plain_completion() -> None:
    reply = await get_chat_model("writer").ainvoke(
        [HumanMessage(content="Reply with the single word OK.")]
    )
    assert "ok" in str(reply.content).lower()


async def test_a_tool_call() -> None:
    @tool
    def capital_of(country: str) -> str:
        """Look up the capital of a country."""
        return "unknown"

    model = get_chat_model("branch").bind_tools([capital_of])
    reply = await model.ainvoke(
        [HumanMessage(content="Use the tool to find the capital of Egypt.")]
    )
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "capital_of"  # type: ignore[attr-defined]  # an AIMessage


async def test_structured_output() -> None:
    answer = await structured_call(
        get_chat_model("analyst"),
        Answer,
        [HumanMessage(content="Give the capital city of Egypt and its country.")],
    )
    assert answer.city.lower() == "cairo" and answer.country.lower() == "egypt"
