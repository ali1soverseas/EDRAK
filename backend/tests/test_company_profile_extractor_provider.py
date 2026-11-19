from pydantic import BaseModel

from edrak.company_profile import extractor


class ExampleSchema(BaseModel):
    value: str


class FakeStructuredModel:
    def __init__(self):
        self.schema = None
        self.method = None

    def with_structured_output(self, schema, *, method):
        self.schema = schema
        self.method = method
        return self

    def invoke(self, messages):
        return ExampleSchema(value="structured result")


class FakeOllamaReply:
    content = '{"value":"ollama result"}'


class FakeOllamaModel:
    def __init__(self):
        self.kwargs = None

    def invoke(self, messages, **kwargs):
        self.kwargs = kwargs
        return FakeOllamaReply()


def test_nvidia_profile_call_uses_structured_output(monkeypatch):
    model = FakeStructuredModel()
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setattr(extractor, "get_chat_model", lambda **kwargs: model)

    result = extractor.schema_caller(ExampleSchema)([{"role": "user", "content": "test"}])

    assert model.schema is ExampleSchema
    assert model.method == "json_schema"
    assert result == '{"value":"structured result"}'


def test_ollama_profile_call_keeps_existing_format_and_reasoning(monkeypatch):
    model = FakeOllamaModel()
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(extractor, "get_chat_model", lambda **kwargs: model)

    result = extractor.schema_caller(ExampleSchema)([{"role": "user", "content": "test"}])

    assert result == '{"value":"ollama result"}'
    assert model.kwargs["format"] == ExampleSchema.model_json_schema()
    assert model.kwargs["reasoning"] == extractor.settings.PROFILE_IMPORT_REASONING