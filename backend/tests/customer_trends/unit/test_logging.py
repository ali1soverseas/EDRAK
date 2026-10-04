import io
import json
from collections.abc import Iterator

import pytest
import structlog

from edrak.agents.customer_trends.logging import configure_logging, get_logger, redact_processor
from edrak.agents.customer_trends.settings import Settings


@pytest.fixture(autouse=True)
def reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def test_sensitive_keys_are_redacted() -> None:
    event = {
        "event": "call",
        "api_key": "a",
        "Authorization": "Bearer abc",
        "x-api-key": "b",
        "access_token": "c",
        "client_secret": "d",
        "keywords": ["gitlab duo"],
        "tokens_used": 12,
    }
    out = redact_processor(None, "info", dict(event))
    assert out["api_key"] == "***"
    assert out["Authorization"] == "***"
    assert out["x-api-key"] == "***"
    assert out["access_token"] == "***"
    assert out["client_secret"] == "***"
    assert out["keywords"] == ["gitlab duo"]
    assert out["tokens_used"] == 12


def test_nested_values_and_bearer_strings_are_scrubbed() -> None:
    out = redact_processor(
        None,
        "info",
        {
            "event": "e",
            "headers": {"authorization": "Bearer abc", "accept": "json"},
            "m": "got Bearer abc.def here",
        },
    )
    assert out["headers"] == {"authorization": "***", "accept": "json"}
    assert "abc.def" not in out["m"]


def test_json_output_in_production_keeps_context_and_redacts() -> None:
    stream = io.StringIO()
    configure_logging(Settings(_env_file=None, edrak_env="production"), stream=stream)
    get_logger("test").info(
        "tool_called", run_id="r1", tool="web_search", api_key="s3cret", q="قهوة"
    )
    line = json.loads(stream.getvalue().strip())
    assert line["event"] == "tool_called"
    assert line["run_id"] == "r1"
    assert line["tool"] == "web_search"
    assert line["api_key"] == "***"
    assert line["q"] == "قهوة"
    assert "s3cret" not in stream.getvalue()
    assert line["level"] == "info"
    assert "timestamp" in line


def test_console_output_in_development_and_level_filter() -> None:
    stream = io.StringIO()
    configure_logging(
        Settings(_env_file=None, edrak_env="development", edrak_log_level="WARNING"), stream=stream
    )
    log = get_logger("test")
    log.info("hidden")
    log.warning("shown", run_id="r2")
    text = stream.getvalue()
    assert "shown" in text
    assert "hidden" not in text
