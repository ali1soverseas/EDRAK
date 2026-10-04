import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from edrak.agents.customer_trends.llm.fake import (
    ScriptedChatModel,
    ScriptExhaustedError,
    tool_call_message,
)


class Answer(BaseModel):
    city: str
    population: int


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


async def test_plays_back_text_in_order_and_records_calls() -> None:
    model = ScriptedChatModel(responses=["one", "two"])
    first = await model.ainvoke([HumanMessage(content="hello")])
    second = await model.ainvoke("again")
    assert (first.content, second.content) == ("one", "two")
    assert len(model.calls) == 2
    assert model.calls[0][0].content == "hello"
    assert model.remaining == 0


async def test_json_payloads_become_json_text_with_unicode_preserved() -> None:
    model = ScriptedChatModel(
        responses=[{"label": "سعر القهوة"}, Answer(city="القاهرة", population=1)]
    )
    first = await model.ainvoke("x")
    second = await model.ainvoke("y")
    assert json.loads(str(first.content)) == {"label": "سعر القهوة"}
    assert "سعر القهوة" in str(first.content)
    assert json.loads(str(second.content)) == {"city": "القاهرة", "population": 1}


async def test_scripted_tool_call_through_bind_tools() -> None:
    model = ScriptedChatModel(responses=[tool_call_message("add", {"a": 2, "b": 3}), "done"])
    bound = model.bind_tools([add])
    reply = await bound.ainvoke("2+3?")
    assert isinstance(reply, AIMessage)
    assert reply.tool_calls[0]["name"] == "add"
    assert reply.tool_calls[0]["args"] == {"a": 2, "b": 3}
    assert model.bound_tool_names == [["add"]]
    assert (await bound.ainvoke("next")).content == "done"


async def test_with_structured_output_returns_validated_instance() -> None:
    model = ScriptedChatModel(
        responses=[{"city": "Cairo", "population": 10}, Answer(city="Giza", population=3)]
    )
    runnable = model.with_structured_output(Answer, method="json_schema")
    first = await runnable.ainvoke("q")
    second = await runnable.ainvoke("q")
    assert first == Answer(city="Cairo", population=10)
    assert second == Answer(city="Giza", population=3)


async def test_structured_output_include_raw_reports_parsing_error() -> None:
    model = ScriptedChatModel(responses=[{"city": "Cairo"}])
    out = await model.with_structured_output(Answer, include_raw=True).ainvoke("q")
    assert out["parsed"] is None
    assert out["parsing_error"] is not None
    assert json.loads(out["raw"].content) == {"city": "Cairo"}


async def test_invalid_structured_output_raises_without_include_raw() -> None:
    model = ScriptedChatModel(responses=[{"city": "Cairo"}])
    with pytest.raises(ValueError, match="invalid"):
        await model.with_structured_output(Answer).ainvoke("q")


async def test_exhausted_script_raises_clear_error() -> None:
    model = ScriptedChatModel(responses=["only"])
    await model.ainvoke("a")
    with pytest.raises(ScriptExhaustedError, match="1 response"):
        await model.ainvoke("b")
    with pytest.raises(ScriptExhaustedError):
        await model.with_structured_output(Answer).ainvoke("c")
