"""OpenTelemetry setup and evaluator-compatible span export for EDRAK."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, ParamSpec, Sequence, TypeVar, cast

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|api[_-]?key|authorization|cookie|credential)", re.IGNORECASE
)
_MAX_TOOL_RESULT_ITEMS = 8
_MAX_TOOL_RESULT_CHARS = 3500
_MAX_TOOL_ARGUMENT_CHARS = 2000
_MAX_TOOL_ARGUMENT_VALUE_CHARS = 300
_SAFE_SPAN_ATTRIBUTES = {
    "edrak.agent.type",
    "edrak.error.type",
    "edrak.query.length",
    "edrak.result.count",
    "edrak.run.resuming",
    "edrak.run.id",
    "edrak.task.id",
    "edrak.task.attempt",
    "edrak.tool.name",
    "edrak.tool.branch",
    "edrak.tool.status",
    "edrak.tool.error_code",
    "edrak.tool.fallback",
    "edrak.tool.provider",
    "edrak.tool.result.count",
    "edrak.tool.argument.names",
    "edrak.tool.argument.types",
    "edrak.tool.argument.values",
    "edrak.tool.result.summary",
    "edrak.tool.result.payload",
    "http.request.method",
    "http.response.status_code",
    "http.route",
}
_provider: TracerProvider | None = None
_configuration_lock = threading.Lock()


@contextmanager
def _export_file_lock(path: Path) -> Iterator[None]:
    """Serialize read/merge/replace across backend processes."""
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            deadline = time.monotonic() + 10
            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for telemetry export lock {lock_path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _default_export_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "telemetry" / f"observability-{os.getpid()}.json"


class JsonFileSpanExporter(SpanExporter):
    """Write compact spans in the shape consumed by ``eval_telemetry``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._spans: list[dict[str, Any]] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    @staticmethod
    def _record(span: ReadableSpan) -> dict[str, Any]:
        status = span.status.status_code if span.status else StatusCode.UNSET
        attrs = span.attributes or {}
        safe_attributes = {
            key: value
            for key, value in attrs.items()
            if key in _SAFE_SPAN_ATTRIBUTES
        }
        record: dict[str, Any] = {
            "name": span.name,
            "trace_id": f"{span.context.trace_id:032x}",
            "span_id": f"{span.context.span_id:016x}",
            "start_time": datetime.fromtimestamp(span.start_time / 1e9, timezone.utc).isoformat(),
            "end_time": datetime.fromtimestamp(span.end_time / 1e9, timezone.utc).isoformat(),
            "duration_ms": max(0.0, (span.end_time - span.start_time) / 1e6),
            "status": "error" if status is StatusCode.ERROR else "ok",
            "attributes": safe_attributes,
        }
        if span.parent:
            record["parent_span_id"] = f"{span.parent.span_id:016x}"
        return record

    def _write(self) -> SpanExportResult:
        temporary: Path | None = None
        try:
            with _export_file_lock(self.path):
                existing: list[dict[str, Any]] = []
                if self.path.exists():
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict) or not isinstance(payload.get("spans"), list):
                        raise ValueError(f"Telemetry export {self.path} must contain a spans list")
                    existing = [span for span in payload["spans"] if isinstance(span, dict)]

                merged = {
                    (span.get("trace_id"), span.get("span_id")): span
                    for span in existing + self._spans
                    if span.get("trace_id") and span.get("span_id")
                }
                spans = list(merged.values())
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary_file:
                    temporary = Path(temporary_file.name)
                    json.dump({"spans": spans}, temporary_file, ensure_ascii=False, indent=2, default=str)

                deadline = time.monotonic() + 2
                while True:
                    try:
                        os.replace(temporary, self.path)
                        temporary = None
                        break
                    except PermissionError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.05)
                self._spans = spans
        except (OSError, ValueError, TimeoutError):
            logger.exception("Could not write OpenTelemetry evaluation export to %s", self.path)
            return SpanExportResult.FAILURE
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return SpanExportResult.SUCCESS

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            self._spans.extend(self._record(span) for span in spans)
            return self._write()

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        with self._lock:
            return self._write() is SpanExportResult.SUCCESS


def configure_telemetry() -> TracerProvider:
    """Configure SDK tracing once; OTLP is enabled when its standard endpoint is set."""
    global _provider
    with _configuration_lock:
        if _provider is not None:
            return _provider

        existing = trace.get_tracer_provider()
        if isinstance(existing, TracerProvider):
            provider = existing
        else:
            provider = TracerProvider(
                resource=Resource.create({"service.name": "edrak-backend"})
            )
            trace.set_tracer_provider(provider)
            if trace.get_tracer_provider() is not provider:
                raise RuntimeError("OpenTelemetry already has a different global tracer provider")

        exporter_path = Path(os.environ.get("EDRAK_TELEMETRY_EXPORT_PATH", _default_export_path()))
        provider.add_span_processor(BatchSpanProcessor(JsonFileSpanExporter(exporter_path)))

        _provider = provider
        logger.info("OpenTelemetry configured; evaluation spans will be written to %s", exporter_path)
        return provider


def flush_telemetry() -> None:
    if _provider is not None and not _provider.force_flush(timeout_millis=5000):
        logger.error("OpenTelemetry span export did not flush before shutdown")


def spans_for_run(analysis_id: str, trace_id: str) -> list[dict[str, Any]]:
    """Return the graph trace and any spans explicitly tagged with this run ID."""
    flush_telemetry()
    export_path = Path(os.environ.get("EDRAK_TELEMETRY_EXPORT_PATH", _default_export_path()))
    try:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read telemetry export {export_path}") from error

    spans = payload.get("spans") if isinstance(payload, dict) else None
    if not isinstance(spans, list):
        raise ValueError(f"Telemetry export {export_path} does not contain a spans list")
    roots = [
        span for span in spans
        if isinstance(span, dict)
        and span.get("trace_id") == trace_id
        and (span.get("attributes") or {}).get("edrak.run.id") == analysis_id
    ]
    if not roots:
        raise LookupError(f"No telemetry root span found for analysis {analysis_id}")
    return [
        span for span in spans
        if isinstance(span, dict)
        and (
            span.get("trace_id") == trace_id
            or (span.get("attributes") or {}).get("edrak.run.id") == analysis_id
        )
    ]


def mark_current_span_error(error: Exception) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        span.set_status(Status(StatusCode.ERROR))
        span.set_attribute("edrak.error.type", type(error).__name__)


def traced(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace a sync or async operation without capturing input or result contents."""
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        tracer = trace.get_tracer("edrak.operations")
        signature = inspect.signature(function)

        @functools.wraps(function)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            with tracer.start_as_current_span(name) as span:
                _set_operation_attributes(span, signature, args, kwargs)
                try:
                    result = function(*args, **kwargs)
                except Exception as error:
                    mark_current_span_error(error)
                    raise
                if isinstance(result, (list, tuple)):
                    span.set_attribute("edrak.result.count", len(result))
                _set_failed_result(span, result)
                return result

        return sync_wrapper

    return decorate


def traced_tool(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace tool identity, argument names, status, and result counts, never argument values."""
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        tracer = trace.get_tracer("edrak.tools")

        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with tracer.start_as_current_span("tool.call") as span:
                    _set_tool_attributes(span, name, function, args, kwargs)
                    try:
                        result = await function(*args, **kwargs)
                    except Exception as error:
                        _set_tool_error(span, error)
                        raise
                    _set_tool_result(span, result)
                    return result

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(function)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            with tracer.start_as_current_span("tool.call") as span:
                _set_tool_attributes(span, name, function, args, kwargs)
                try:
                    result = function(*args, **kwargs)
                except Exception as error:
                    _set_tool_error(span, error)
                    raise
                _set_tool_result(span, result)
                return result

        return sync_wrapper

    return decorate


def _redact_argument_value(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return "[TRUNCATED]"
    if isinstance(value, str):
        return value[:_MAX_TOOL_ARGUMENT_VALUE_CHARS]
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _redact_argument_value(item, depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_argument_value(item, depth + 1) for item in list(value)[:_MAX_TOOL_RESULT_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def redact_tool_argument_values(value: Any) -> Any:
    """Return bounded, credential-redacted tool arguments for trusted local displays."""
    return _redact_argument_value(value)


def _set_tool_attributes(
    span: Any, name: str, function: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    span.set_attribute("edrak.tool.name", name)
    span.set_attribute("edrak.tool.status", "ok")
    bound = inspect.signature(function).bind_partial(*args, **kwargs)
    argument_names: set[str] = set()
    argument_types: dict[str, str] = {}
    argument_values: dict[str, Any] = {}
    for key, value in bound.arguments.items():
        if key in {"self", "cls", "ctx", "tool_obj"}:
            continue
        if isinstance(value, dict):
            argument_names.update(map(str, value))
            argument_types.update({str(name): type(item).__name__ for name, item in value.items()})
            argument_values.update({str(name): item for name, item in value.items()})
        elif hasattr(value, "model_fields") and callable(getattr(value, "model_dump", None)):
            fields = value.model_dump(exclude={"run_id", "task_id"})
            argument_names.update(map(str, fields))
            argument_types.update({str(name): type(item).__name__ for name, item in fields.items()})
            argument_values.update({str(name): item for name, item in fields.items()})
        else:
            argument_names.add(key)
            argument_types[key] = type(value).__name__
            argument_values[key] = value
    if argument_values and os.environ.get(
        "EDRAK_TELEMETRY_ARGUMENT_VALUES", "1"
    ).strip().lower() not in {"0", "false", "no", "off"}:
        values = json.dumps(
            {
                key: "[REDACTED]" if _SECRET_KEY.search(key) else _redact_argument_value(item)
                for key, item in argument_values.items()
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if len(values) > _MAX_TOOL_ARGUMENT_CHARS:
            values = json.dumps({"truncated": True, "serialized_length": len(values)}, separators=(",", ":"))
        span.set_attribute("edrak.tool.argument.values", values)
    if argument_names:
        span.set_attribute("edrak.tool.argument.names", ",".join(sorted(argument_names)))
        span.set_attribute(
            "edrak.tool.argument.types",
            json.dumps(argument_types, sort_keys=True, separators=(",", ":")),
        )


def set_tool_provider(provider: str, *, fallback: bool = False) -> None:
    """Record which concrete backend (e.g. tavily, serper) served the current tool call."""
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("edrak.tool.provider", provider)
        if fallback:
            span.set_attribute("edrak.tool.fallback", True)


def set_tool_outcome(status: str, *, error_code: str | None = None) -> None:
    """Record a tool's returned status when failures are represented as response objects."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_attribute("edrak.tool.status", status)
    if error_code:
        span.set_attribute("edrak.tool.error_code", error_code)
    if status.lower() in {"error", "failed", "failure", "timeout"}:
        span.set_status(Status(StatusCode.ERROR))


def _set_tool_error(span: Any, error: Exception) -> None:
    span.set_attribute("edrak.tool.status", "error")
    span.set_attribute("edrak.tool.error_code", type(error).__name__)
    mark_current_span_error(error)


def _tool_result_value(result: Any) -> Any:
    if hasattr(result, "model_dump") and callable(result.model_dump):
        return result.model_dump(mode="json")
    return result


def _tool_result_shape(result: Any) -> dict[str, Any]:
    value = _tool_result_value(result)
    decoded_json = False
    if isinstance(value, str):
        try:
            value = json.loads(value)
            decoded_json = True
        except json.JSONDecodeError:
            return {"type": "string", "length": len(value), "empty": not value, "decoded_json": False}

    if isinstance(value, (list, tuple)):
        sample = value[:_MAX_TOOL_RESULT_ITEMS]
        keys = {
            str(key)
            for item in sample
            if isinstance(item, dict)
            for key in item
            if not _SECRET_KEY.search(str(key))
        }
        return {
            "type": "array",
            "count": len(value),
            "item_types": sorted({type(item).__name__ for item in sample}),
            "item_keys": sorted(keys)[:20],
            "decoded_json": decoded_json,
        }
    if isinstance(value, dict):
        safe_keys = [str(key) for key in value if not _SECRET_KEY.search(str(key))]
        collection_counts = {
            str(key): len(item)
            for key, item in value.items()
            if str(key) in safe_keys and isinstance(item, (list, tuple))
        }
        return {
            "type": "object",
            "keys": sorted(safe_keys)[:20],
            "field_count": len(value),
            "collection_counts": collection_counts,
            "decoded_json": decoded_json,
        }
    if isinstance(value, str):
        return {"type": "string", "length": len(value), "empty": not value, "decoded_json": decoded_json}
    return {"type": type(value).__name__, "decoded_json": decoded_json}


def _redact_tool_result(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return "[TRUNCATED]"
    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        try:
            return _redact_tool_result(json.loads(value), depth + 1)
        except json.JSONDecodeError:
            return value[:1000]
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key))
            else _redact_tool_result(item, depth + 1)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        return [_redact_tool_result(item, depth + 1) for item in value[:_MAX_TOOL_RESULT_ITEMS]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def _set_tool_result(span: Any, result: Any) -> None:
    result_shape = _tool_result_shape(result)
    if result_shape.get("type") == "array" and isinstance(result_shape.get("count"), int):
        span.set_attribute("edrak.tool.result.count", result_shape["count"])
    span.set_attribute(
        "edrak.tool.result.summary",
        json.dumps(result_shape, sort_keys=True, separators=(",", ":")),
    )
    if os.environ.get("EDRAK_TELEMETRY_INCLUDE_TOOL_PAYLOADS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        payload = json.dumps(
            _redact_tool_result(result),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(payload) > _MAX_TOOL_RESULT_CHARS:
            payload = json.dumps(
                {
                    "truncated": True,
                    "type": type(result).__name__,
                    "serialized_length": len(payload),
                },
                separators=(",", ":"),
            )
        span.set_attribute("edrak.tool.result.payload", payload)


def _set_operation_attributes(
    span: Any,
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    bound = signature.bind_partial(*args, **kwargs)
    query = bound.arguments.get("query")
    if isinstance(query, str):
        span.set_attribute("edrak.query.length", len(query))
    operation_input = bound.arguments.get("task")
    if operation_input is None and args:
        operation_input = args[0]
    if operation_input is None:
        return
    run_id = getattr(operation_input, "parent_request_id", None)
    if isinstance(run_id, str) and run_id:
        span.set_attribute("edrak.run.id", run_id)
    worker = getattr(operation_input, "worker", None)
    if worker is not None:
        worker_type = getattr(worker, "value", worker)
        span.set_attribute("edrak.agent.type", str(worker_type))
    attempt = getattr(operation_input, "attempt", None)
    if isinstance(attempt, int):
        span.set_attribute("edrak.task.attempt", attempt)
    task_id = getattr(operation_input, "task_id", None)
    if isinstance(task_id, str) and task_id:
        span.set_attribute("edrak.task.id", task_id)


def _set_failed_result(span: Any, result: Any) -> None:
    status = getattr(result, "status", None)
    status = getattr(status, "value", status)
    if isinstance(status, str) and status.lower() in {"failed", "error", "timeout"}:
        span.set_status(Status(StatusCode.ERROR))
        span.set_attribute("edrak.error.type", "OperationFailure")

