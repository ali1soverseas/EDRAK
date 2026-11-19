from langchain_openai import ChatOpenAI

from edrak.core import llm


def test_nvidia_key_prefers_agent_key_and_backup(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "shared-test-key")
    monkeypatch.setenv("NVIDIA_API_KEY_MARKET", "market-test-key")
    monkeypatch.setenv("NVIDIA_API_KEY_MARKET_BACKUP", "market-backup-test-key")

    assert llm.provider_key("NVIDIA_API_KEY", "market") == (
        "market-test-key",
        "NVIDIA_API_KEY_MARKET",
    )
    monkeypatch.delenv("NVIDIA_API_KEY_MARKET")
    assert llm.provider_key("NVIDIA_API_KEY", "market") == (
        "market-backup-test-key",
        "NVIDIA_API_KEY_MARKET_BACKUP",
    )


def test_nvidia_provider_builds_openai_compatible_client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-test-key")
    monkeypatch.setenv("NVIDIA_MODEL", "test-nvidia-model")

    client = llm.get_chat_model(agent="market")

    assert isinstance(client, ChatOpenAI)
    assert client.model_name == "test-nvidia-model"
    assert str(client.openai_api_base).rstrip("/") == (
        "https://integrate.api.nvidia.com/v1"
    )


def test_openai_provider_can_override_global_nvidia(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvidia-test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    client = llm.get_chat_model(
        model="gpt-4o-mini", agent="competitor", provider="openai"
    )

    assert isinstance(client, ChatOpenAI)
    assert client.model_name == "gpt-4o-mini"
    assert client.openai_api_key.get_secret_value() == "openai-test-key"


def test_competitor_kb_selects_openai_only(monkeypatch):
    from edrak.knowledge import competitor_kb

    received = {}

    def fake_get_structured(schema, **kwargs):
        received.update(kwargs)
        return object()

    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("COMPETITOR_KB_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(llm, "get_structured", fake_get_structured)

    competitor_kb._kb_structured(competitor_kb.ProfileDraft, timeout=180)

    assert received == {
        "model": "gpt-4o-mini",
        "agent": "competitor",
        "temperature": 0,
        "timeout": 180,
        "provider": "openai",
    }
