from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import Settings


def create_chat_model(settings: Settings) -> BaseChatModel:
    """Create the LangChain chat model configured for TraceMind RAG V2."""
    if settings.llm_base_url is None or settings.llm_model is None:
        raise ValueError("LLM_BASE_URL and LLM_MODEL must be configured")

    extra_body = (
        {"enable_thinking": settings.llm_enable_thinking}
        if settings.llm_enable_thinking is not None
        else None
    )
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=(
            settings.llm_api_key.get_secret_value()
            if settings.llm_api_key is not None
            else "not-required"
        ),
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        use_responses_api=False,
        extra_body=extra_body,
    )
