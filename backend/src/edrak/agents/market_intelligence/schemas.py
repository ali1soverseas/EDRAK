"""Pydantic shapes for every market-agent model reply."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, field_validator


def _clean_list(value):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return value
    return [str(item).strip() for item in value if str(item).strip()]


class PlannedTask(BaseModel):
    description: str = Field(min_length=1)
    tool_hint: str = Field(min_length=1)


class TaskPlan(BaseModel):
    tasks: list[PlannedTask] = Field(min_length=1)


class QueryArgs(BaseModel):
    queries: list[str] = Field(min_length=1)

    @field_validator("queries", mode="before")
    @classmethod
    def _queries(cls, value):
        return _clean_list(value)


class WorldBankArgs(BaseModel):
    country: str = Field(min_length=3, description="ISO3 country code chosen for this task.")
    indicator_codes: list[str] = Field(min_length=1, description="World Bank indicator codes.")

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("indicator_codes", mode="before")
    @classmethod
    def _codes(cls, value):
        return _clean_list(value)


class IndicatorArgs(BaseModel):
    indicator_codes: list[str] = Field(min_length=1)

    @field_validator("indicator_codes", mode="before")
    @classmethod
    def _codes(cls, value):
        return _clean_list(value)


class SeriesArgs(BaseModel):
    series_ids: list[str] = Field(min_length=1)

    @field_validator("series_ids", mode="before")
    @classmethod
    def _ids(cls, value):
        return _clean_list(value)


class DatasetArgs(BaseModel):
    dataset_codes: list[str] = Field(min_length=1)

    @field_validator("dataset_codes", mode="before")
    @classmethod
    def _codes(cls, value):
        return _clean_list(value)


class TickerArgs(BaseModel):
    tickers: list[str] = Field(min_length=1)

    @field_validator("tickers", mode="before")
    @classmethod
    def _tickers(cls, value):
        return _clean_list(value)


class CikArgs(BaseModel):
    cik_numbers: list[str] = Field(min_length=1)

    @field_validator("cik_numbers", mode="before")
    @classmethod
    def _ciks(cls, value):
        return _clean_list(value)


class Usefulness(BaseModel):
    useful: bool
    reason: str = ""


class AnalysisClaim(BaseModel):
    claim: str = Field(min_length=1)


TOOL_ARG_MODELS: dict[str, type[BaseModel]] = {
    "tool_worldbank": WorldBankArgs,
    "tool_imf": IndicatorArgs,
    "tool_sec_edgar": CikArgs,
    "tool_fred": SeriesArgs,
    "tool_eurostat": DatasetArgs,
    "tool_alphavantage": TickerArgs,
    "tool_finnhub": TickerArgs,
}


def schema_text(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2)
