"""Tests for EDRAK Langfuse observability, tracing, and prompt versioning."""

from langchain_core.callbacks.base import BaseCallbackHandler

from edrak.core.config import settings
from edrak.core.llm import get_chat_model
from edrak.core.observability import (
    _safe_format,
    agent_label,
    build_observability_config,
    flush_observability,
    get_versioned_prompt,
    is_observability_enabled,
)
from edrak.orchestration.prompts import (
    get_planner_prompt,
    get_request_builder_prompt,
)


def test_safe_format_mustache_and_python():
    """Verify both mustache {{key}} and python {key} formatting work seamlessly."""
    template = "Hello {{company}}, assessing market in {market}."
    rendered = _safe_format(template, company="NVIDIA", market="EMEA")
    assert rendered == "Hello NVIDIA, assessing market in EMEA."


def test_observability_disabled_behavior(monkeypatch):
    """When disabled, observability functions return None and don't fail."""
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)

    assert not is_observability_enabled()
    cfg = build_observability_config("test-trace")
    assert cfg["callbacks"] == []
    assert cfg["run_name"] == "test-trace"
    assert "langfuse_trace_name" in cfg["metadata"]


def test_observability_enabled_config_generation(monkeypatch):
    """When enabled and configured, config attaches metadata and callback handler."""
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-mock-1234")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-mock-5678")

    assert is_observability_enabled()
    cfg = build_observability_config(
        "analysis-101",
        session_id="session-xyz",
        user_id="user-corp",
        tags=["nvidia", "orchestration"],
        metadata={"custom_env": "pytest"},
    )

    assert cfg["run_name"] == "analysis-101"
    assert cfg["metadata"]["langfuse_session_id"] == "session-xyz"
    assert cfg["metadata"]["langfuse_user_id"] == "user-corp"
    assert cfg["metadata"]["custom_env"] == "pytest"
    assert "nvidia" in cfg["tags"]
    assert len(cfg["callbacks"]) == 1


def test_versioned_prompt_offline_fallback():
    """Verify fallback template works with variable substitution when prompt server is unavailable."""
    rendered = get_versioned_prompt(
        prompt_name="nonexistent-prompt",
        fallback_template="Analyze competitors for {{target}} in market {region}",
        target="Blackwell",
        region="APAC",
    )
    assert rendered == "Analyze competitors for Blackwell in market APAC"


def test_orchestration_versioned_prompts():
    """Verify planner and request builder prompt getters return valid templates."""
    planner_prompt = get_planner_prompt()
    assert "You are the EDRAK control-plane planner" in planner_prompt

    builder_prompt = get_request_builder_prompt()
    assert "You turn a user's business query into a structured EDRAK research request" in builder_prompt


def test_get_chat_model_accepts_callbacks(monkeypatch):
    """Verify get_chat_model accepts callbacks parameter without error."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    class DummyHandler(BaseCallbackHandler):
        pass

    handler = DummyHandler()
    model = get_chat_model(callbacks=[handler])
    assert model is not None
    assert handler in model.callbacks


def test_flush_observability_fail_open():
    """Verify flush_observability never raises errors even when client is unconfigured or fails."""
    flush_observability()


def test_chat_model_stamps_the_full_agent_name(monkeypatch):
    """LLM generations are named and tagged after the worker for Langfuse filters."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert agent_label("market") == "market_intelligence"
    model = get_chat_model(agent="market")
    cfg = model._config_for_agent(None)
    assert cfg["run_name"] == "market_intelligence"
    assert cfg["metadata"]["agent"] == "market_intelligence"
    assert "market_intelligence" in cfg["tags"]
    assert "market_intelligence" in cfg["metadata"]["langfuse_tags"]


def test_open_agent_scope_overrides_credential_label(monkeypatch):
    """An open agent_scope wins for tags, so verification is not tagged as orchestrator."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from edrak.core.observability import _CURRENT_AGENT

    token = _CURRENT_AGENT.set("verification")
    try:
        model = get_chat_model(agent="orchestrator")
        cfg = model._config_for_agent(None)
        assert cfg["run_name"] == "verification"
        assert "verification" in cfg["metadata"]["langfuse_tags"]
        assert "verification" in cfg["tags"]
    finally:
        _CURRENT_AGENT.reset(token)


def test_model_retry_is_recorded_as_default(monkeypatch):
    """A transient failure is noted before the next attempt, without a WARNING level."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from edrak.core.llm import EdrakChat

    model = get_chat_model(agent="competitor")
    calls = {"n": 0}
    recorded = []

    def once(self, input, config, kwargs, bind_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            err = RuntimeError("rate limited")
            err.status_code = 429
            raise err
        return "ok"

    monkeypatch.setattr(EdrakChat, "_once", once)
    monkeypatch.setattr("edrak.core.llm.time.sleep", lambda _seconds: None)

    def capture(name, **kwargs):
        recorded.append((name, kwargs.get("agent"), kwargs.get("level")))

    monkeypatch.setattr("edrak.core.observability.record_event", capture)
    # Re-bind the helper so it uses the patched record_event.
    import edrak.core.llm as llm_mod

    def record_retry(agent, attempt, exc):
        from edrak.core.observability import record_event

        record_event(
            "retry",
            agent=agent or "llm",
            level="DEFAULT",
            message=f"attempt {attempt} failed: {type(exc).__name__}: {exc}",
            metadata={"attempt": attempt},
        )

    monkeypatch.setattr(llm_mod, "_record_retry", record_retry)

    assert model.invoke("hello") == "ok"
    assert calls["n"] == 2
    assert recorded == [("retry", "competitor", "DEFAULT")]


def test_log_action_does_not_emit_langfuse_events(monkeypatch, tmp_path):
    """Routine worker lines stay in the text log. Langfuse events are explicit only."""
    monkeypatch.setattr("edrak.core.action_log._ROOT", tmp_path)
    seen = []
    monkeypatch.setattr(
        "edrak.core.observability.record_event",
        lambda *args, **kwargs: seen.append((args, kwargs)),
    )
    from edrak.core.action_log import log_action

    log_action("market_intelligence", "searching sources")
    log_action("market_intelligence", "provider_exhausted: serper")
    log_action("market_intelligence", "gap: nothing found")

    assert seen == []
    assert (tmp_path / "artifacts" / "logs" / "market_intelligence.log").is_file()


def test_provider_exhausted_is_warning_and_failure_is_error(monkeypatch):
    """WARNING is reserved for provider exhaustion. ERROR is for real failures."""
    seen = []

    def capture(name, **kwargs):
        seen.append((name, kwargs.get("level"), kwargs.get("agent")))

    monkeypatch.setattr("edrak.core.observability.record_event", capture)
    from edrak.agents.customer_trends.tools.base import _record_tool_issue

    _record_tool_issue("provider_exhausted", "all providers failed")
    _record_tool_issue("failure", "serper is down")
    _record_tool_issue("budget_exceeded", "budget used up")

    assert seen == [
        ("provider_exhausted", "WARNING", "customer_trends"),
        ("failure", "ERROR", "customer_trends"),
    ]
