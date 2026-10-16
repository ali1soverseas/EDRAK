"""structlog setup: JSON in production, console otherwise, with secret redaction."""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO

import structlog

from edrak.agents.customer_trends.settings import Settings, get_settings

_SENSITIVE_WORDS = frozenset(
    {"token", "key", "apikey", "secret", "authorization", "password", "credential"}
)
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_REDACTED = "***"


def _is_sensitive(key: str) -> bool:
    return any(word in _SENSITIVE_WORDS for word in re.split(r"[_\-\s.]+", key.lower()))


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _REDACTED if _is_sensitive(str(k)) else _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _BEARER.sub(f"Bearer {_REDACTED}", value)
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace the value of any sensitive-looking key and mask bearer credentials."""
    for key in list(event_dict):
        event_dict[key] = _REDACTED if _is_sensitive(key) else _scrub(event_dict[key])
    return event_dict


def configure_logging(settings: Settings | None = None, *, stream: TextIO | None = None) -> None:
    s = settings or get_settings()
    level = logging.getLevelNamesMapping().get(s.edrak_log_level.upper(), logging.INFO)
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if s.is_production
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not structlog.is_configured():
        configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
