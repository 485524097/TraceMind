import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.llm.factory import create_chat_model


@pytest.mark.parametrize(
    ("enable_thinking", "expected_extra_body"),
    [
        (True, {"enable_thinking": True}),
        (False, {"enable_thinking": False}),
        (None, None),
    ],
)
def test_create_chat_model_maps_settings_to_chat_openai(
    enable_thinking: bool | None,
    expected_extra_body: dict[str, bool] | None,
) -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="test-api-key",
        llm_model="test-model",
        llm_timeout_seconds=12,
        llm_temperature=0.2,
        llm_max_tokens=345,
        llm_enable_thinking=enable_thinking,
    )

    model = create_chat_model(settings)

    assert isinstance(model, BaseChatModel)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "test-model"
    assert model.openai_api_base == "http://localhost:11434/v1"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "test-api-key"
    assert model.request_timeout == 12
    assert model.temperature == 0.2
    assert model.max_tokens == 345
    assert model.use_responses_api is False
    assert model.extra_body == expected_extra_body
    assert "enable_thinking" not in model.model_kwargs


def test_create_chat_model_uses_non_empty_placeholder_without_api_key() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="http://localhost:11434/v1",
        llm_model="test-model",
    )

    model = create_chat_model(settings)

    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "not-required"


def test_create_chat_model_requires_configured_endpoint_and_model() -> None:
    with pytest.raises(ValueError, match="LLM_BASE_URL and LLM_MODEL"):
        create_chat_model(Settings(_env_file=None))
