import pytest
from pydantic import ValidationError

from edrak.agents.market_intelligence.schemas import (
    CikArgs,
    IndicatorArgs,
    QueryArgs,
    TaskPlan,
    WorldBankArgs,
)


def test_query_args_accept_a_single_string():
    parsed = QueryArgs.model_validate({"queries": "AI DevSecOps demand"})
    assert parsed.queries == ["AI DevSecOps demand"]


def test_worldbank_requires_country_and_codes():
    with pytest.raises(ValidationError):
        WorldBankArgs.model_validate({"queries": ["GDP"]})
    parsed = WorldBankArgs.model_validate(
        {"country": "usa", "indicator_codes": ["NY.GDP.MKTP.KD.ZG"]}
    )
    assert parsed.country == "USA"
    assert parsed.model_dump()["indicator_codes"] == ["NY.GDP.MKTP.KD.ZG"]


def test_sec_and_imf_reject_search_queries():
    with pytest.raises(ValidationError):
        CikArgs.model_validate({"queries": ["GitLab"]})
    with pytest.raises(ValidationError):
        IndicatorArgs.model_validate({"queries": ["NGDPD"]})
    assert CikArgs.model_validate({"cik_numbers": ["0001653482"]}).cik_numbers == ["0001653482"]


def test_task_plan_schema_names_its_fields():
    schema = TaskPlan.model_json_schema()
    assert "tasks" in schema["properties"]
