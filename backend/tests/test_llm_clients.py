import types as pytypes

import pytest

from app.diagnosis.models import CauseCategory, Diagnosis, FixAction, RiskLevel, SuggestedFix
from app.llm.base import LLMRateLimitError, LLMResponseError
from app.llm.factory import get_llm_client
from app.llm.gemini_client import FREE_TIER_MODELS, MAX_OUTPUT_TOKENS, GeminiClient

SAMPLE_DIAGNOSIS = Diagnosis(
    cause_category=CauseCategory.RENAME,
    likely_cause="column relabeled upstream",
    suggested_fix=SuggestedFix(action=FixAction.RENAME_COLUMN),
    risk_level=RiskLevel.LOW,
    confidence=0.9,
)


# ---- GeminiClient ----


def test_gemini_client_rejects_non_free_tier_model():
    with pytest.raises(ValueError, match="free-tier"):
        GeminiClient(api_key="fake-key", model="gemini-2.5-pro")


def test_gemini_client_accepts_every_free_tier_model():
    for model in FREE_TIER_MODELS:
        client = GeminiClient(api_key="fake-key", model=model)
        assert client.model_name == model


def test_gemini_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiClient(model="gemini-3.5-flash")


def test_gemini_client_defaults_to_flash_model(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    client = GeminiClient(api_key="fake-key")
    assert client.model_name == "gemini-3.5-flash"


def test_gemini_client_temperature_is_zero():
    assert GeminiClient(api_key="fake-key", model="gemini-3.5-flash").temperature == 0.0


def test_gemini_client_complete_returns_parsed_diagnosis(monkeypatch):
    client = GeminiClient(api_key="fake-key", model="gemini-3.5-flash")

    fake_response = pytypes.SimpleNamespace(parsed=SAMPLE_DIAGNOSIS, text="{}")
    captured_config = {}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured_config["model"] = model
            captured_config["config"] = config
            return fake_response

    client._client = pytypes.SimpleNamespace(models=FakeModels())

    result = client.complete("system prompt", "user prompt", Diagnosis)

    assert result == SAMPLE_DIAGNOSIS
    assert captured_config["model"] == "gemini-3.5-flash"
    assert captured_config["config"].temperature == 0.0
    assert captured_config["config"].response_mime_type == "application/json"
    assert captured_config["config"].max_output_tokens == MAX_OUTPUT_TOKENS


def test_gemini_client_maps_429_to_rate_limit_error(monkeypatch):
    from google.genai import errors

    client = GeminiClient(api_key="fake-key", model="gemini-3.5-flash")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ClientError(code=429, response_json={"error": {"message": "quota exceeded"}})

    client._client = pytypes.SimpleNamespace(models=FakeModels())

    with pytest.raises(LLMRateLimitError):
        client.complete("s", "u", Diagnosis)


def test_gemini_client_maps_other_api_errors_to_response_error():
    from google.genai import errors

    client = GeminiClient(api_key="fake-key", model="gemini-3.5-flash")

    class FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ClientError(code=400, response_json={"error": {"message": "bad request"}})

    client._client = pytypes.SimpleNamespace(models=FakeModels())

    with pytest.raises(LLMResponseError):
        client.complete("s", "u", Diagnosis)


# ---- GeminiClient: multi-key rotation ----


def test_gemini_client_reads_multiple_keys_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", " key-1 , key-2 ,key-3")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = GeminiClient(model="gemini-3.5-flash")
    assert client._api_keys == ["key-1", "key-2", "key-3"]


def test_gemini_client_falls_back_to_single_key_env_when_no_multi(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "solo-key")
    client = GeminiClient(model="gemini-3.5-flash")
    assert client._api_keys == ["solo-key"]


def test_gemini_client_explicit_api_key_overrides_env_key_list(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "key-1,key-2")
    client = GeminiClient(api_key="explicit-key", model="gemini-3.5-flash")
    assert client._api_keys == ["explicit-key"]


def test_gemini_client_rotates_to_next_key_on_429_then_succeeds():
    from google.genai import errors

    client = GeminiClient(api_key="key-a", model="gemini-3.5-flash")
    client._api_keys = ["key-a", "key-b"]

    class FakeModelsExhausted:
        def generate_content(self, **kwargs):
            raise errors.ClientError(code=429, response_json={"error": {"message": "quota exceeded"}})

    class FakeModelsWorking:
        def generate_content(self, *, model, contents, config):
            return pytypes.SimpleNamespace(parsed=SAMPLE_DIAGNOSIS, text="{}")

    fakes = {
        "key-a": pytypes.SimpleNamespace(models=FakeModelsExhausted()),
        "key-b": pytypes.SimpleNamespace(models=FakeModelsWorking()),
    }
    client._get_client = lambda: fakes[client._api_keys[client._key_index]]

    result = client.complete("s", "u", Diagnosis)

    assert result == SAMPLE_DIAGNOSIS
    assert client._key_index == 1


def test_gemini_client_raises_only_after_every_key_exhausted():
    from google.genai import errors

    client = GeminiClient(api_key="key-a", model="gemini-3.5-flash")
    client._api_keys = ["key-a", "key-b"]

    class FakeModelsExhausted:
        def generate_content(self, **kwargs):
            raise errors.ClientError(code=429, response_json={"error": {"message": "quota exceeded"}})

    fake = pytypes.SimpleNamespace(models=FakeModelsExhausted())
    client._get_client = lambda: fake

    with pytest.raises(LLMRateLimitError):
        client.complete("s", "u", Diagnosis)

    assert client._key_index == 1  # both keys were tried before giving up


def test_gemini_client_falls_back_to_parsing_raw_text_when_unparsed():
    client = GeminiClient(api_key="fake-key", model="gemini-3.5-flash")
    fake_response = pytypes.SimpleNamespace(parsed=None, text=SAMPLE_DIAGNOSIS.model_dump_json())

    class FakeModels:
        def generate_content(self, **kwargs):
            return fake_response

    client._client = pytypes.SimpleNamespace(models=FakeModels())

    result = client.complete("s", "u", Diagnosis)
    assert result == SAMPLE_DIAGNOSIS


def test_gemini_client_malformed_text_raises_response_error():
    client = GeminiClient(api_key="fake-key", model="gemini-3.5-flash")
    fake_response = pytypes.SimpleNamespace(parsed=None, text="not json at all")

    class FakeModels:
        def generate_content(self, **kwargs):
            return fake_response

    client._client = pytypes.SimpleNamespace(models=FakeModels())

    with pytest.raises(LLMResponseError):
        client.complete("s", "u", Diagnosis)


# ---- get_llm_client() provider selection ----
# Gemini is the only provider this deployment runs. These are deliberately
# NOT about the no-LLM degrade path (get_diagnostic_agent etc catching
# construction failure and returning None) - that's provider-agnostic and
# covered elsewhere (tests/test_no_llm_mode.py). These are specifically
# about what get_llm_client() itself does with LLM_PROVIDER's value.


def test_get_llm_client_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    client = get_llm_client()
    assert isinstance(client, GeminiClient)


def test_get_llm_client_rejects_ollama_explicitly(monkeypatch):
    # Not just "unknown provider" - Ollama used to be a real, working value
    # here. Removing it must fail loudly and specifically, never silently
    # fall through to some default.
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER 'ollama'; expected 'gemini'"):
        get_llm_client()


def test_get_llm_client_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        get_llm_client()
