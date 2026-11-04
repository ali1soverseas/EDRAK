"""Centralized LLM usage tracking.

The tracker is exercised through a real LangChain model run where it matters
(callbacks firing, a reply passing through unchanged) and through the handler
directly where a network call would buy nothing.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from edrak.usage import context, html_report, pricing, report, store
from edrak.usage.tracker import UsageCallback
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


@pytest.fixture
def usage_log(tmp_path, monkeypatch):
    """A fresh log writing into this test's tmp dir, so tests never share a file."""
    monkeypatch.setenv("EDRAK_LLM_USAGE_DIR", str(tmp_path))
    monkeypatch.setenv("EDRAK_LLM_TRACKING", "1")
    fresh = store.UsageLog()
    monkeypatch.setattr(store, "log", fresh)
    yield tmp_path
    fresh.flush()


def _response(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model: str = "gpt-oss:120b",
    response_metadata: dict | None = None,
) -> ChatResult:
    usage = None
    if input_tokens is not None or output_tokens is not None:
        usage = {
            "input_tokens": input_tokens or 0,
            "output_tokens": output_tokens or 0,
            "total_tokens": (input_tokens or 0) + (output_tokens or 0),
        }
    meta = {"model": model}
    if response_metadata:
        meta.update(response_metadata)
    message = AIMessage(content="ok", usage_metadata=usage, response_metadata=meta)
    return ChatResult(generations=[ChatGeneration(message=message)])


class _FakeChatModel(BaseChatModel):
    """A chat model that answers instantly, for driving the callback machinery."""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return _response(input_tokens=11, output_tokens=7)


def _only(records: list[dict]) -> dict:
    assert len(records) == 1, records
    return records[0]


# --------------------------------------------------------------------------- #
# Successful and failed calls
# --------------------------------------------------------------------------- #
def test_success_recorded_with_tokens_and_estimated_cost(usage_log, monkeypatch):
    # Asserts Ollama's published-rate pricing specifically, whichever provider
    # the environment (or .env during a trial) selects. gpt-oss:120b is priced
    # at $0.15 input / $0.60 output per million (ollama.com/pricing), so
    # 120 input + 80 output estimates at $0.000066.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    callback = UsageCallback()
    run_id = uuid4()
    callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "gpt-oss:120b"})
    callback.on_llm_end(
        _response(input_tokens=120, output_tokens=80, response_metadata={"eval_duration": 2_000_000_000, "total_duration": 3_000_000_000}),
        run_id=run_id,
    )
    store.flush()

    record = _only(store.read_records())
    assert record["status"] == "success"
    assert record["input_tokens"] == 120
    assert record["output_tokens"] == 80
    assert record["total_tokens"] == 200
    assert record["generation_ms"] == 2000.0
    assert record["generation_tps"] == 40.0
    assert record["latency_ms"] == 3000.0
    assert record["pricing_known"] is True
    assert record["estimated_cost_usd"] == pytest.approx(0.000066)
    assert record["pricing_source"] == "configured"


def test_failed_request_recorded_without_tokens(usage_log):
    callback = UsageCallback()
    run_id = uuid4()
    callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "gpt-oss:120b"})
    callback.on_llm_error(TimeoutError("too slow"), run_id=run_id)
    store.flush()

    record = _only(store.read_records())
    assert record["status"] == "failure"
    assert record["error_type"] == "TimeoutError"
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None


def test_missing_usage_metadata_is_unknown_not_zero(usage_log):
    callback = UsageCallback()
    run_id = uuid4()
    callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "some-unpriced-model"})
    callback.on_llm_end(_response(model="some-unpriced-model"), run_id=run_id)
    store.flush()

    record = _only(store.read_records())
    assert record["input_tokens"] is None
    assert record["estimated_cost_usd"] is None
    assert record["pricing_known"] is False


def test_context_labels_the_call(usage_log):
    callback = UsageCallback()
    run_id = uuid4()
    with context.usage_context(run_id="req-9", use_case="competitive_intelligence", stage="competitor", operation="discovery"):
        callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "gpt-oss:120b"})
        callback.on_llm_end(_response(input_tokens=5, output_tokens=5), run_id=run_id)
    store.flush()

    record = _only(store.read_records())
    assert record["run_id"] == "req-9"
    assert record["use_case"] == "competitive_intelligence"
    assert record["stage"] == "competitor"
    assert record["operation"] == "discovery"


def test_langgraph_node_names_the_operation(usage_log):
    """A call with no explicit label inherits the LangGraph node it came from."""
    callback = UsageCallback()
    run_id = uuid4()
    callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "gpt-oss:120b"}, metadata={"langgraph_node": "stage_1"})
    callback.on_llm_end(_response(input_tokens=5, output_tokens=5), run_id=run_id)
    store.flush()

    assert _only(store.read_records())["operation"] == "stage_1"


def test_explicit_operation_wins_over_the_graph_node(usage_log):
    callback = UsageCallback()
    run_id = uuid4()
    with context.usage_context(stage="evaluation", operation="eval_faithfulness"):
        callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "gpt-oss:120b"}, metadata={"langgraph_node": "judge"})
        callback.on_llm_end(_response(input_tokens=5, output_tokens=5), run_id=run_id)
    store.flush()

    record = _only(store.read_records())
    assert record["operation"] == "eval_faithfulness"
    assert record["stage"] == "evaluation"


def test_retry_attempt_is_recorded_separately(usage_log):
    callback = UsageCallback()
    for attempt in (1, 2):
        run_id = uuid4()
        with context.usage_context(run_id="req-1", stage="market", attempt=attempt):
            callback.on_chat_model_start({}, [], run_id=run_id, invocation_params={"model": "gpt-oss:120b"})
            callback.on_llm_end(_response(input_tokens=10, output_tokens=10), run_id=run_id)
    store.flush()

    records = store.read_records()
    assert [r["retry_attempt"] for r in records] == [0, 1]


# --------------------------------------------------------------------------- #
# Concurrency and persistence
# --------------------------------------------------------------------------- #
def test_concurrent_workers_do_not_lose_records(usage_log):
    def worker(index: int) -> None:
        for i in range(25):
            store.emit({"call_id": f"{index}-{i}", "run_id": "req-1", "status": "success"})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.flush()
    assert len(store.read_records()) == 200


def test_records_persist_to_the_log_file(usage_log):
    store.emit({"call_id": "c1", "run_id": "req-1", "status": "success"})
    assert store.flush()
    assert (usage_log / "llm_calls.jsonl").is_file()
    assert (usage_log / "llm_calls.log").is_file()
    assert store.read_records()[0]["call_id"] == "c1"


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #
def test_cost_is_calculated_from_billable_tokens(monkeypatch):
    monkeypatch.setenv("LLM_PRICE_TESTMODEL_INPUT_PER_MILLION", "1.0")
    monkeypatch.setenv("LLM_PRICE_TESTMODEL_OUTPUT_PER_MILLION", "2.0")
    pricing.reload()
    try:
        cost, known, source = pricing.estimate_cost("ollama", "testmodel", 1000, 500)
        assert known is True
        assert source == "configured"
        assert cost == pytest.approx(0.002)
    finally:
        pricing.reload()


def test_cached_tokens_are_not_double_counted(monkeypatch):
    monkeypatch.setenv("LLM_PRICE_TESTMODEL_INPUT_PER_MILLION", "1.0")
    monkeypatch.setenv("LLM_PRICE_TESTMODEL_OUTPUT_PER_MILLION", "0.0")
    monkeypatch.setenv("LLM_PRICE_TESTMODEL_CACHED_INPUT_PER_MILLION", "0.25")
    pricing.reload()
    try:
        # 1000 input of which 400 cached: 600 billable at 1.0 + 400 at 0.25.
        cost, known, _ = pricing.estimate_cost("ollama", "testmodel", 1000, 0, 400)
        assert known is True
        assert cost == pytest.approx((600 * 1.0 + 400 * 0.25) / 1_000_000)
    finally:
        pricing.reload()


def test_unpriced_model_reports_unknown():
    assert pricing.price_for("ollama", "no-such-model") is None
    cost, known, source = pricing.estimate_cost("ollama", "no-such-model", 10, 10)
    assert cost is None and known is False and source is None


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _emit(run_id: str, stage: str, in_tok: int, out_tok: int, *, latency: float, failure: bool = False, retry: int = 0,
          operation: str | None = None, unpriced: bool = False,
          timestamp: str = "2026-01-01T00:00:00.000000Z") -> None:
    store.emit(
        {
            "call_id": uuid4().hex,
            "run_id": run_id,
            "use_case": "competitive_intelligence",
            "stage": stage,
            "operation": operation,
            "provider": "ollama",
            "model": "gpt-oss:120b",
            "timestamp": timestamp,
            "retry_attempt": retry,
            "status": "failure" if failure else "success",
            "error_type": "Boom" if failure else None,
            "input_tokens": None if failure else in_tok,
            "output_tokens": None if failure else out_tok,
            "total_tokens": None if failure else in_tok + out_tok,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "latency_ms": latency,
            "app_elapsed_ms": latency,
            "generation_ms": latency,
            "generation_tps": None,
            "estimated_cost_usd": None if unpriced else 0.0,
            "pricing_known": not unpriced,
            "pricing_source": None if unpriced else "free_tier",
        }
    )


def test_workflow_aggregates_across_workers(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000)
    _emit("req-1", "competitor", 100, 50, latency=3000, retry=1)
    _emit("req-1", "internal", 200, 100, latency=2000)
    _emit("req-1", "market", 0, 0, latency=500, failure=True)
    _emit("req-2", "internal", 10, 10, latency=100)
    store.flush()

    summary = report.workflow_summary("req-1")
    assert summary["calls"] == 4
    assert summary["retries"] == 1
    assert summary["failures"] == 1
    assert summary["input_tokens"] == 400
    assert summary["output_tokens"] == 200
    assert summary["total_tokens"] == 600
    assert summary["workflow_seconds"] is not None

    stages = {row["name"]: row for row in summary["stages"]}
    assert stages["competitor"]["calls"] == 2
    assert stages["competitor"]["retries"] == 1
    assert stages["market"]["failures"] == 1
    assert stages["internal"]["total_tokens"] == 300


def test_system_aggregates_by_model_and_use_case(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000)
    _emit("req-2", "internal", 100, 50, latency=3000)
    store.flush()

    summary = report.system_summary()
    assert summary["calls"] == 2
    assert summary["success"] == 2
    assert summary["runs"] == 2
    assert summary["total_tokens"] == 300
    assert summary["avg_latency_ms"] == pytest.approx(2000.0)
    assert summary["p95_latency_ms"] == pytest.approx(3000.0)
    assert summary["by_model"][0]["name"] == "gpt-oss:120b"
    assert summary["by_use_case"][0]["name"] == "competitive_intelligence"


def test_system_aggregates_by_stage_and_operation(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="discovery")
    _emit("req-1", "competitor", 100, 50, latency=3000, retry=1, operation="stage_1")
    _emit("req-1", "verification", 10, 10, latency=500)
    store.flush()

    summary = report.system_summary()
    stages = {row["name"]: row for row in summary["by_stage"]}
    assert stages["competitor"]["calls"] == 2
    assert stages["competitor"]["retries"] == 1

    operations = {row["name"]: row for row in summary["by_operation"]}
    assert operations["discovery"]["calls"] == 1
    assert operations["stage_1"]["retries"] == 1
    assert operations["stage_1"]["total_tokens"] == 150


def test_a_dimension_no_record_captured_says_so(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000)
    store.flush()

    rendered = report.render_system()
    assert "by stage" in rendered
    assert "by operation: not captured in these records" in rendered


def test_a_partly_captured_dimension_still_adds_up(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="discovery")
    _emit("req-1", "competitor", 100, 50, latency=1000)  # no operation recorded
    store.flush()

    summary = report.system_summary()
    operations = {row["name"]: row for row in summary["by_operation"]}
    assert operations["discovery"]["calls"] == 1
    assert operations["-"]["calls"] == 1
    assert sum(row["calls"] for row in summary["by_operation"]) == summary["calls"]

    assert "(not captured)" in report.render_system(unicode=True)
    assert "(not captured)" in html_report.render_system_html()


def test_workflow_summary_breaks_down_by_operation(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="discovery")
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="stage_1")
    store.flush()

    operations = {row["name"]: row for row in report.workflow_summary("req-1")["operations"]}
    assert set(operations) == {"discovery", "stage_1"}


def test_breakdown_tables_show_retries_and_failures(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, retry=1, operation="stage_1")
    _emit("req-1", "competitor", 0, 0, latency=500, failure=True, operation="stage_1")
    store.flush()

    rendered = report.render_system(unicode=True)
    assert "retry" in rendered
    row = next(r for r in report.system_summary()["by_stage"] if r["name"] == "competitor")
    assert (row["retries"], row["failures"]) == (1, 1)


def test_throughput_falls_back_to_end_to_end_latency(usage_log):
    # Ollama Cloud reports total_duration but leaves eval_duration null, so the
    # generation-only basis is unavailable and the fallback must say so.
    store.emit(
        {
            "call_id": "c1",
            "run_id": "req-1",
            "use_case": None,
            "stage": "competitor",
            "provider": "ollama",
            "model": "gpt-oss:120b",
            "timestamp": "2026-01-01T00:00:00.000000Z",
            "retry_attempt": 0,
            "status": "success",
            "error_type": None,
            "input_tokens": 10,
            "output_tokens": 100,
            "total_tokens": 110,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "latency_ms": 2000.0,
            "app_elapsed_ms": 2000.0,
            "generation_ms": None,
            "generation_tps": None,
            "estimated_cost_usd": 0.0,
            "pricing_known": True,
            "pricing_source": "free_tier",
        }
    )
    store.flush()

    summary = report.workflow_summary("req-1")
    assert summary["throughput_tps"] == pytest.approx(50.0)
    assert summary["throughput_basis"] == "end-to-end"


def test_render_workflow_is_a_boxed_report(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000)
    store.flush()
    rendered = report.render_workflow("req-1")
    assert "req-1" in rendered
    assert "competitor" in rendered
    assert "TOTAL" in rendered
    assert report.render_workflow("nope").startswith("No LLM calls recorded")


# --------------------------------------------------------------------------- #
# Wiring: callbacks really fire, and a reply is unchanged
# --------------------------------------------------------------------------- #
def test_callbacks_fire_through_a_langchain_invoke(usage_log):
    model = _FakeChatModel().with_config({"callbacks": [UsageCallback()]})
    reply = model.invoke("hi")
    store.flush()

    assert reply.content == "ok"
    record = _only(store.read_records())
    assert record["input_tokens"] == 11
    assert record["output_tokens"] == 7


def test_a_real_langgraph_node_labels_its_calls(usage_log):
    """End to end: the node name LangGraph puts in the metadata becomes the operation."""
    from langgraph.graph import END, START, StateGraph

    model = _FakeChatModel().with_config({"callbacks": [UsageCallback()]})

    def node(state):
        return {"out": model.invoke("hi").content}

    graph = StateGraph(dict)
    graph.add_node("synthesize", node)
    graph.add_edge(START, "synthesize")
    graph.add_edge("synthesize", END)
    graph.compile().invoke({})
    store.flush()

    record = _only(store.read_records())
    assert record["operation"] == "synthesize"


def test_get_chat_model_attaches_the_tracker(monkeypatch):
    from edrak.core import llm

    # Pins the default provider: the factory test runs under ollama even while
    # a trial provider (e.g. groq through .env) is configured.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setattr(llm, "ChatOllama", lambda **kwargs: kwargs)
    built = llm.get_chat_model(agent="competitor")

    callbacks = built.get("callbacks")
    assert isinstance(callbacks, list)
    assert any(isinstance(cb, UsageCallback) for cb in callbacks)


def test_get_chat_model_groq_branch_attaches_the_tracker(monkeypatch):
    from edrak.core import llm

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    # The repository .env carries per-agent scoped keys, and a scoped key
    # legitimately beats the shared one. Remove them so this test exercises the
    # shared fallback it is actually about.
    monkeypatch.delenv("GROQ_API_KEY_COMPETITOR", raising=False)
    monkeypatch.delenv("GROQ_API_KEY_COMPETITOR_BACKUP", raising=False)
    monkeypatch.setattr(llm, "ChatOllama", lambda **kwargs: kwargs)
    captured = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "langchain_openai",
                        __import__("types").SimpleNamespace(ChatOpenAI=_FakeChatOpenAI))
    built = llm.get_chat_model(agent="competitor")

    assert captured["base_url"] == llm.GROQ_BASE_URL
    assert captured["api_key"] == "test-groq-key"
    callbacks = captured["callbacks"]
    assert isinstance(callbacks, list)
    assert any(isinstance(cb, UsageCallback) for cb in callbacks)


# --------------------------------------------------------------------------- #
# Credential fallback added for the shared *_BACKUP variables
# --------------------------------------------------------------------------- #
def test_provider_key_uses_a_shared_backup_last(monkeypatch):
    from edrak.core.llm import provider_key

    monkeypatch.delenv("TAVILY_API_KEY_FALLBACKTEST", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY_FALLBACKTEST_AGENT", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY_FALLBACKTEST_AGENT_BACKUP", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY_FALLBACKTEST_BACKUP", "shared-backup")

    value, variable = provider_key("TAVILY_API_KEY_FALLBACKTEST", "agent")
    assert value == "shared-backup"
    assert variable == "TAVILY_API_KEY_FALLBACKTEST_BACKUP"


def test_provider_key_prefers_scoped_over_shared_backup(monkeypatch):
    from edrak.core.llm import provider_key

    monkeypatch.setenv("TAVILY_API_KEY_FALLBACKTEST2_BACKUP", "shared-backup")
    monkeypatch.setenv("TAVILY_API_KEY_FALLBACKTEST2_AGENT_BACKUP", "scoped-backup")

    value, variable = provider_key("TAVILY_API_KEY_FALLBACKTEST2", "agent")
    assert value == "scoped-backup"
    assert variable == "TAVILY_API_KEY_FALLBACKTEST2_AGENT_BACKUP"


# --------------------------------------------------------------------------- #
# HTML pages: the same figures as the terminal report, as a file
# --------------------------------------------------------------------------- #
def test_html_index_shows_the_same_breakdowns_as_the_cli(usage_log):
    _emit("req-1", "competitor_intelligence", 100, 50, latency=1000, operation="discovery")
    _emit("req-1", "metrics", 200, 60, latency=2000, retry=1, operation="harvest")
    store.flush()

    page = html_report.render_index_html()

    assert "by stage" in page
    assert "by operation" in page
    assert "competitor_intelligence" in page
    assert "discovery" in page
    assert "harvest" in page
    assert "<th>calls</th>" in page
    assert "<th>retry</th>" in page
    assert "<th>fail</th>" in page


def test_html_run_page_has_a_total_row_and_the_totals(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="discovery")
    _emit("req-1", "metrics", 200, 60, latency=2000, operation="harvest")
    store.flush()

    page = html_report.render_run_html("req-1")

    assert "run req-1" in page
    assert "by stage" in page
    assert "by operation" in page
    assert "<strong>TOTAL</strong>" in page
    assert "discovery" in page


def test_html_says_unknown_rather_than_zero_when_a_call_is_unpriced(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, unpriced=True)
    store.flush()

    page = html_report.render_system_html()

    assert "unknown" in page
    assert "$0.00" not in page


def test_html_says_so_when_a_dimension_was_never_captured(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000)  # no operation recorded
    store.flush()

    page = html_report.render_system_html()

    assert "by stage" in page
    assert "by operation: not captured in these records" in page


def test_html_escapes_record_data(usage_log):
    _emit("req-1", "<script>alert(1)</script>", 10, 5, latency=100, operation="<b>x</b>")
    store.flush()

    page = html_report.render_index_html()

    assert "<script>" not in page
    assert "<b>x</b>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_html_index_writes_a_page_per_run_and_links_resolve(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="discovery")
    _emit("req-2", "metrics", 100, 50, latency=1000, operation="harvest")
    store.flush()

    written = html_report.write_index(usage_log)

    assert {"index.html", "system.html"} <= {path.name for path in written}
    assert {"run-req-1.html", "run-req-2.html"} <= {path.name for path in written}
    assert all(path.is_file() for path in written)

    index = (usage_log / "index.html").read_text(encoding="utf-8")
    assert 'href="run-req-1.html"' in index
    assert 'href="run-req-2.html"' in index


def test_html_run_file_name_is_safe_for_a_pathological_run_id():
    assert html_report.run_file_name("../../etc/passwd") == "run-.._.._etc_passwd.html"
    assert html_report.run_file_name("a/b") == "run-a_b.html"


def test_html_run_file_name_says_what_an_opaque_run_was():
    records = [
        {"run_id": "6c9ec1441b90eda66da1069071c7bf93", "use_case": "competitive_intelligence",
         "model": "qwen/qwen3.8-27b", "stage": "supervisor", "status": "success"},
        {"run_id": "6c9ec1441b90eda66da1069071c7bf93", "use_case": "competitive_intelligence",
         "model": "qwen/qwen3.8-27b", "stage": "metrics", "status": "success"},
        {"run_id": "6c9ec1441b90eda66da1069071c7bf93", "use_case": "competitive_intelligence",
         "model": "gpt-oss:120b", "stage": "cross_signal", "status": "failure"},
    ]

    name = html_report.run_file_name("6c9ec1441b90eda66da1069071c7bf93", records)

    # Use case and the majority model, then enough of the id to stay unique.
    assert name == "run-competitive-intelligence-qwen3.8-27b-6c9ec144.html"

    # A run with no successful call is named after the label its failures carry,
    # and says that it failed.
    failed = [
        {"run_id": "5d41834f7af4c4361e74ae35e6068f9d", "use_case": "competitive_intelligence",
         "model": "gpt-oss:120b", "stage": "metrics", "status": "failure"},
    ]
    assert (
        html_report.run_file_name("5d41834f7af4c4361e74ae35e6068f9d", failed)
        == "run-competitive-intelligence-gpt-oss-120b-failed-5d41834f.html"
    )


def test_html_run_file_name_keeps_a_speaking_id(usage_log):
    records = [{"run_id": "smoke-qwen-paced", "use_case": None, "model": "qwen/qwen3.8-27b"}]

    assert html_report.run_file_name("smoke-qwen-paced", records) == "run-smoke-qwen-paced.html"


def test_run_ids_are_distinct_and_newest_first(usage_log):
    _emit("run-old", "competitor", 10, 5, latency=100, timestamp="2026-01-01T00:00:00.000000Z")
    _emit("run-old", "competitor", 10, 5, latency=100, timestamp="2026-01-01T00:00:05.000000Z")
    _emit("run-new", "competitor", 10, 5, latency=100, timestamp="2026-01-02T00:00:00.000000Z")
    store.flush()

    assert report.run_ids() == ["run-new", "run-old"]


def test_html_on_an_empty_log_says_so(usage_log):
    assert "No LLM calls recorded yet" in html_report.render_index_html()
    assert "No LLM calls recorded for run" in html_report.render_run_html("nope")


# --------------------------------------------------------------------------- #
# Per agent: one block per worker, in the order each first called a model
# --------------------------------------------------------------------------- #
def test_stage_details_orders_agents_and_operations_as_they_ran(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="search",
          timestamp="2026-01-01T00:00:05.000000Z")
    _emit("req-1", "internal", 10, 5, latency=100, operation="plan_queries",
          timestamp="2026-01-01T00:00:01.000000Z")
    _emit("req-1", "competitor", 100, 50, latency=3000, retry=1, operation="search",
          timestamp="2026-01-01T00:00:09.000000Z")
    _emit("req-1", "competitor", 20, 10, latency=200, operation="synthesize",
          timestamp="2026-01-01T00:00:02.000000Z")
    store.flush()

    details = report.stage_details(run_id="req-1")

    # Agents in the order they first ran, not alphabetically or by spend.
    assert [detail["name"] for detail in details] == ["internal", "competitor"]
    assert details[0]["calls"] == 1
    assert details[1]["calls"] == 3
    assert details[1]["retries"] == 1
    assert details[1]["total_tokens"] == 330
    # Operations in the order this agent worked through them, not by cost.
    assert [row["name"] for row in details[1]["operations"]] == ["synthesize", "search"]
    # Every call, in sequence, with its own retry marker.
    assert [call["operation"] for call in details[1]["calls_detail"]] == [
        "synthesize",
        "search",
        "search",
    ]
    assert [call["retry_attempt"] for call in details[1]["calls_detail"]] == [0, 0, 1]
    assert details[1]["started_at"] == "2026-01-01T00:00:02.000000Z"
    assert details[1]["ended_at"] == "2026-01-01T00:00:09.000000Z"


def test_stage_details_can_be_limited_to_one_run(usage_log):
    _emit("req-1", "competitor", 10, 5, latency=100, operation="search")
    _emit("req-2", "market", 10, 5, latency=100, operation="grid")
    store.flush()

    assert [detail["name"] for detail in report.stage_details(run_id="req-1")] == ["competitor"]
    assert [detail["name"] for detail in report.stage_details(run_id="req-2")] == ["market"]
    assert len(report.stage_details()) == 2


def test_html_run_page_shows_every_agent_through_the_run(usage_log):
    _emit("req-1", "internal", 10, 5, latency=100, operation="plan_queries",
          timestamp="2026-01-01T00:00:01.000000Z")
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="search",
          timestamp="2026-01-01T00:00:05.000000Z")
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="synthesize",
          timestamp="2026-01-01T00:00:06.000000Z")
    store.flush()

    page = html_report.render_run_html("req-1")

    assert "agents through the run" in page
    assert 'id="agent-internal"' in page
    assert 'id="agent-competitor"' in page
    assert '<a href="#agent-internal">internal</a>' in page
    assert "<h4>by operation</h4>" in page
    assert "<h4>calls</h4>" in page
    assert "<th>operation</th>" in page
    # Agents appear in run order, and each agent's own operations sit in its block.
    assert page.index('id="agent-internal"') < page.index('id="agent-competitor"')
    internal_block = page.split('id="agent-competitor"')[0].split('id="agent-internal"')[1]
    assert "plan_queries" in internal_block
    assert "synthesize" not in internal_block


def test_html_agent_section_handles_a_lone_failed_call(usage_log):
    _emit("req-1", "market", 0, 0, latency=250, failure=True,
          timestamp="2026-01-01T00:00:01.000000Z")
    store.flush()

    page = html_report.render_run_html("req-1")

    assert "agents through the run" in page
    assert 'id="agent-market"' in page
    assert "failure" in page
    # The agent's calls carry no operation, and the report says so rather than printing a dash.
    assert "(not captured)" in page


def test_html_run_page_still_says_unknown_for_an_unpriced_agent_call(usage_log):
    _emit("req-1", "competitor", 100, 50, latency=1000, operation="search", unpriced=True)
    store.flush()

    page = html_report.render_run_html("req-1")

    assert "agents through the run" in page
    assert "unknown" in page
    assert "$0.00" not in page

def test_prompt_estimate_counts_every_message(monkeypatch):
    from edrak.core import llm
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [[SystemMessage(content="x" * 350), HumanMessage(content="y" * 350)]]

    # 700 characters at 3.5 chars per token.
    assert llm.estimate_prompt_tokens(messages) == 200


def test_groq_bucket_paces_a_burst_and_refuses_an_oversized_prompt(monkeypatch):
    from edrak.core import llm

    clock = {"now": 1000.0}
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(llm.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s))

    bucket = llm._GroqBucket()

    # The first request spends the minute...
    bucket.acquire(llm._GROQ_TOKEN_BUDGET)
    # ...so the next one has to wait for the window to roll over.
    bucket.acquire(100)
    assert clock["now"] >= 1000.0 + llm._GROQ_WINDOW_S

    with pytest.raises(RuntimeError, match="split it"):
        bucket.acquire(llm._GROQ_TOKEN_BUDGET + 1)


def test_groq_models_are_paced_per_credential(monkeypatch):
    from edrak.core import llm

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY_MARKET", "test-key")

    model = llm.get_chat_model(agent="market")
    paced = [c for c in model.callbacks or [] if isinstance(c, llm._GroqPaceCallback)]

    assert paced and paced[0].credential == "GROQ_API_KEY_MARKET"


def test_get_structured_forwards_the_reply_ceiling(monkeypatch):
    from edrak.core import llm

    seen = {}

    class FakeModel:
        def with_structured_output(self, schema, method=None):
            seen["schema"] = schema
            seen["method"] = method
            return "bound"

    def fake_get_chat_model(model=None, *, agent=None, temperature=None, num_predict=None):
        seen.update(model=model, agent=agent, temperature=temperature, num_predict=num_predict)
        return FakeModel()

    monkeypatch.setattr(llm, "get_chat_model", fake_get_chat_model)

    assert llm.get_structured(dict, agent="metrics", temperature=0, num_predict=512) == "bound"
    assert seen == {
        "model": None,
        "agent": "metrics",
        "temperature": 0,
        "num_predict": 512,
        "schema": dict,
        "method": "json_schema",
    }
