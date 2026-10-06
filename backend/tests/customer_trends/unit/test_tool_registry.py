import json
from datetime import date
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from edrak.agents.customer_trends.schemas.common import ToolStatus
from edrak.agents.customer_trends.store.evidence_store import EvidenceStore
from edrak.agents.customer_trends.tools.registry import (
    ALL_SPECS,
    BRANCH_TOOLS,
    COLLECTION_SPECS,
    PROCESSING_SPECS,
    STAGE_TOOLS,
    build_tools,
    compact_json,
    model_visible_schema,
    tools_for_branch,
    tools_for_stage,
)
from edrak.agents.customer_trends.tools.search_interest import trend_direction
from tests.customer_trends.factories import RUN_ID
from tests.customer_trends.tool_helpers import (
    evidence_result,
    make_context,
    processing_context,
    serves,
)

SPEC_TOOL_NAMES = [
    "web_search",
    "fetch_page",
    "social_search",
    "social_comments",
    "search_interest",
    "reviews_fetch",
    "news_coverage",
]
PROCESSING_TOOL_NAMES = ["analyze_text", "compute_metrics", "evidence_query", "submit_findings"]
ALL_TOOL_NAMES = [*SPEC_TOOL_NAMES, *PROCESSING_TOOL_NAMES]


@pytest.fixture
def tools(store: EvidenceStore) -> dict[str, StructuredTool]:
    ctx, _ = make_context(
        store, "social_search:reddit", serves(evidence_result("social_search:reddit"))
    )
    return build_tools(ctx)


def test_the_eleven_tools_have_the_names_of_the_spec(
    tools: dict[str, StructuredTool],
) -> None:
    assert list(tools) == ALL_TOOL_NAMES
    assert [spec.name for spec in COLLECTION_SPECS] == SPEC_TOOL_NAMES
    assert [spec.name for spec in PROCESSING_SPECS] == PROCESSING_TOOL_NAMES
    assert [spec.name for spec in ALL_SPECS] == ALL_TOOL_NAMES
    assert all(isinstance(tool, StructuredTool) for tool in tools.values())


def test_branch_subsets_are_exactly_as_specified(store: EvidenceStore) -> None:
    ctx, _ = make_context(
        store, "social_search:reddit", serves(evidence_result("social_search:reddit"))
    )
    assert [t.name for t in tools_for_branch("social", ctx)] == [
        "social_search",
        "social_comments",
        "web_search",
    ]
    assert [t.name for t in tools_for_branch("demand", ctx)] == [
        "search_interest",
        "news_coverage",
        "web_search",
    ]
    assert [t.name for t in tools_for_branch("reviews", ctx)] == [
        "reviews_fetch",
        "web_search",
        "fetch_page",
    ]
    assert set(BRANCH_TOOLS) == {"social", "demand", "reviews"}
    with pytest.raises(ValueError, match="unknown branch"):
        tools_for_branch("internal", ctx)


async def test_a_branch_tool_reports_its_branch_in_events(store: EvidenceStore) -> None:
    events: list[dict[str, Any]] = []
    ctx, _ = make_context(
        store,
        "social_search:reddit",
        serves(evidence_result("social_search:reddit")),
        emit=events.append,
    )
    [search, *_] = tools_for_branch("social", ctx)
    await search.ainvoke({"platform": "reddit", "query": "gitlab duo"})
    assert events[0]["branch"] == "social"
    assert ctx.branch is None


@pytest.mark.parametrize("name", ALL_TOOL_NAMES)
def test_the_model_visible_schema_hides_run_and_task_ids(
    tools: dict[str, StructuredTool], name: str
) -> None:
    schema = tools[name].args_schema
    assert isinstance(schema, dict)
    assert "run_id" not in schema["properties"] and "task_id" not in schema["properties"]
    assert "run_id" not in schema.get("required", []) and "task_id" not in schema.get(
        "required", []
    )
    assert "run_id" not in tools[name].args
    wire = json.dumps(convert_to_openai_tool(tools[name]))
    assert "run_id" not in wire and "task_id" not in wire


@pytest.mark.parametrize("name", ALL_TOOL_NAMES)
def test_the_model_visible_schema_is_flat_and_describes_every_field(
    tools: dict[str, StructuredTool], name: str
) -> None:
    schema = tools[name].args_schema
    assert isinstance(schema, dict)
    dumped = json.dumps(schema)
    for marker in ("$ref", "$defs", "anyOf", '"title"', '"default": null'):
        assert marker not in dumped, marker
    assert all("description" in prop for prop in schema["properties"].values())


def test_required_fields_are_the_ones_the_model_must_supply(
    tools: dict[str, StructuredTool],
) -> None:
    required = {name: tool.args_schema.get("required", []) for name, tool in tools.items()}  # type: ignore[union-attr]  # dict schema
    assert required == {
        "web_search": ["query"],
        "fetch_page": ["url"],
        "social_search": ["platform", "query"],
        "social_comments": ["platform", "post_url"],
        "search_interest": ["keywords"],
        "reviews_fetch": ["store", "target_id_or_url"],
        "news_coverage": ["query"],
        "analyze_text": ["batch_ids"],
        "compute_metrics": ["metric"],
        "evidence_query": [],
        "submit_findings": ["findings"],
    }


def test_enums_and_limits_are_visible_to_the_model(tools: dict[str, StructuredTool]) -> None:
    social = tools["social_search"].args_schema["properties"]  # type: ignore[index]  # dict schema
    assert social["platform"]["enum"] == [
        "x",
        "reddit",
        "tiktok",
        "instagram",
        "facebook",
        "youtube",
    ]
    assert social["sort"]["enum"] == ["recent", "top"]
    interest = tools["search_interest"].args_schema["properties"]  # type: ignore[index]  # dict schema
    assert (interest["keywords"]["minItems"], interest["keywords"]["maxItems"]) == (1, 5)
    assert tools["reviews_fetch"].args_schema["properties"]["store"]["enum"] == [  # type: ignore[index]  # dict schema
        "app_store",
        "google_play",
        "amazon",
    ]


@pytest.mark.parametrize("name", ALL_TOOL_NAMES)
def test_descriptions_say_when_to_use_when_not_to_and_give_an_example(
    tools: dict[str, StructuredTool], name: str
) -> None:
    description = tools[name].description
    assert "Use it" in description
    assert "Do NOT" in description
    assert "Example: {" in description
    example = description.split("Example: ", 1)[1]
    arguments = json.loads(example[: example.rindex("}") + 1].replace("\n", " "))
    assert isinstance(arguments, dict) and arguments
    assert len(description) < 1800


@pytest.mark.parametrize("name", ALL_TOOL_NAMES)
def test_every_example_in_a_description_is_valid_input(name: str) -> None:
    spec = next(s for s in ALL_SPECS if s.name == name)
    example = spec.description.split("Example: ", 1)[1]
    arguments = json.loads(example[: example.rindex("}") + 1].replace("\n", " "))
    spec.input_model.model_validate({**arguments, "run_id": RUN_ID, "task_id": "t"})


async def test_a_structured_tool_returns_compact_json_and_injects_the_ids(
    store: EvidenceStore,
) -> None:
    ctx, provider = make_context(
        store, "social_search:reddit", serves(evidence_result("social_search:reddit"))
    )
    tool = build_tools(ctx)["social_search"]
    output = await tool.ainvoke({"platform": "reddit", "query": "gitlab duo", "run_id": "mine"})
    data = json.loads(output)
    assert isinstance(output, str)
    assert data["status"] == ToolStatus.OK.value and data["count"] == 3
    assert provider.calls[0]["run_id"] == RUN_ID
    assert store.run_summary(RUN_ID).evidence_count == 3
    assert "text" not in output.replace('"snippet"', "")


async def test_a_structured_tool_reports_bad_arguments_as_json_not_exceptions(
    store: EvidenceStore,
) -> None:
    ctx, _ = make_context(
        store, "social_search:reddit", serves(evidence_result("social_search:reddit"))
    )
    output = await build_tools(ctx)["social_search"].ainvoke({"platform": "myspace", "query": "q"})
    data = json.loads(output)
    assert data["status"] == "error" and data["error_code"] == "invalid_input"


def test_model_visible_schema_of_a_model_with_no_optional_fields_has_no_required_list_noise() -> (
    None
):
    from edrak.agents.customer_trends.tools.fetch_page import FetchPageInput

    schema = model_visible_schema(FetchPageInput)
    assert schema["required"] == ["url"] and set(schema["properties"]) == {"url", "max_chars"}


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([10, 20, 35, 50, 70, 90], "up"),
        ([90, 70, 50, 35, 20, 10], "down"),
        ([50, 51, 49, 50, 52, 50], "flat"),
        ([10, 100, 12, 95, 11, 98], "up"),
        ([40, 55, 45, 60, 50, 80, 85], "up"),
        ([0, 0, 0, 0], "flat"),
        ([30], "flat"),
        ([], "flat"),
        ([100, 100, 100], "flat"),
        ([100, 96, 98, 95, 97], "flat"),
        ([100, 94, 90, 85, 80], "down"),
    ],
)
def test_trend_direction_uses_a_slope_with_a_tolerance(values: list[float], expected: str) -> None:
    points = [(date(2026, 1, 1 + i), float(v)) for i, v in enumerate(values)]
    assert trend_direction(points) == expected


def test_trend_direction_tolerance_can_be_tightened() -> None:
    points = [(date(2026, 1, 1 + i), float(v)) for i, v in enumerate([100, 98, 97, 96])]
    assert trend_direction(points) == "flat"
    assert trend_direction(points, tolerance=0.01) == "down"


def test_the_stages_get_exactly_their_processing_tools(store: EvidenceStore) -> None:
    ctx, _ = make_context(
        store, "social_search:reddit", serves(evidence_result("social_search:reddit"))
    )
    assert [t.name for t in tools_for_stage("analyze", ctx)] == ["compute_metrics", "analyze_text"]
    assert [t.name for t in tools_for_stage("write", ctx)] == ["evidence_query", "compute_metrics"]
    assert set(STAGE_TOOLS) == {"analyze", "write"}
    with pytest.raises(ValueError, match="unknown stage"):
        tools_for_stage("verify", ctx)


def test_the_processing_tools_are_in_no_collection_branch() -> None:
    in_branches = {name for names in BRANCH_TOOLS.values() for name in names}
    assert not in_branches & set(PROCESSING_TOOL_NAMES)


async def test_a_processing_tool_injects_the_ids_and_answers_in_compact_json(
    loaded_store: EvidenceStore,
) -> None:
    ctx = processing_context(loaded_store)
    output = await build_tools(ctx)["compute_metrics"].ainvoke(
        {"metric": "language_mix", "run_id": "mine", "task_id": "mine"}
    )
    data = json.loads(output)
    assert isinstance(output, str)
    assert data["status"] == "ok" and data["count"] == 40
    assert data["values"]["counts"] == {"en": 25, "ar": 15}
    assert data["metric_id"].startswith("m_") and data["metric"] == "language_mix"
    assert "warnings" not in data and "params" not in data
    assert loaded_store.get_metric(RUN_ID, data["metric_id"]) is not None


async def test_a_processing_tool_reports_bad_arguments_as_json_not_exceptions(
    loaded_store: EvidenceStore,
) -> None:
    ctx = processing_context(loaded_store)
    output = await build_tools(ctx)["submit_findings"].ainvoke({"findings": []})
    data = json.loads(output)
    assert data["status"] == "error" and data["error_code"] == "invalid_input"


async def test_the_model_can_query_evidence_through_the_tool(loaded_store: EvidenceStore) -> None:
    ctx = processing_context(loaded_store)
    output = await build_tools(ctx)["evidence_query"].ainvoke(
        {"filters": {"platform": "tiktok"}, "limit": 2}
    )
    data = json.loads(output)
    assert data["count"] == 2 and data["total"] == 3
    assert {item["platform"] for item in data["items"]} == {"tiktok"}


def test_compact_json_keeps_status_and_count_and_drops_what_is_empty() -> None:
    from edrak.agents.customer_trends.schemas.common import ProcessingResponse

    assert json.loads(compact_json(ProcessingResponse())) == {"status": "ok", "count": 0}


def test_a_field_of_several_scalar_types_is_one_type_list_for_the_model() -> None:
    from edrak.agents.customer_trends.tools.submit_findings import SubmitFindingsInput

    schema = model_visible_schema(SubmitFindingsInput)
    metrics = schema["properties"]["findings"]["items"]["properties"]["metrics"]
    assert metrics["additionalProperties"] == {"type": ["number", "integer", "string"]}
    assert "anyOf" not in json.dumps(schema)
