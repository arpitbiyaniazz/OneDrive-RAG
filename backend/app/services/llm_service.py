import logging
from typing import Optional, NamedTuple
from openai import AsyncOpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMClientInfo(NamedTuple):
    client: AsyncOpenAI
    model: str
    provider: str


def get_llm_client() -> Optional[LLMClientInfo]:
    """
    Returns configured LLM client, model name, and provider name.
    Supports Mistral AI (api.mistral.ai) and OpenAI.
    """
    provider = (settings.LLM_PROVIDER or "mistral").lower().strip()

    # 1. Mistral provider
    if provider == "mistral":
        api_key = settings.MISTRAL_API_KEY.strip() if settings.MISTRAL_API_KEY else ""
        if api_key and not api_key.startswith("your-"):
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=settings.MISTRAL_API_BASE or "https://api.mistral.ai/v1",
            )
            model = settings.MISTRAL_MODEL or "mistral-small-latest"
            return LLMClientInfo(client=client, model=model, provider="mistral")

        # Fallback to OpenAI if Mistral key is empty but OpenAI key is set
        openai_key = settings.OPENAI_API_KEY.strip() if settings.OPENAI_API_KEY else ""
        if openai_key and not openai_key.startswith("your-"):
            logger.info("MISTRAL_API_KEY not set; falling back to OpenAI")
            client = AsyncOpenAI(api_key=openai_key)
            model = settings.OPENAI_MODEL or "gpt-4o-mini"
            return LLMClientInfo(client=client, model=model, provider="openai")

    # 2. OpenAI provider
    elif provider == "openai":
        openai_key = settings.OPENAI_API_KEY.strip() if settings.OPENAI_API_KEY else ""
        if openai_key and not openai_key.startswith("your-"):
            client = AsyncOpenAI(api_key=openai_key)
            model = settings.OPENAI_MODEL or "gpt-4o-mini"
            return LLMClientInfo(client=client, model=model, provider="openai")

        # Fallback to Mistral if OpenAI key is empty but Mistral key is set
        api_key = settings.MISTRAL_API_KEY.strip() if settings.MISTRAL_API_KEY else ""
        if api_key and not api_key.startswith("your-"):
            logger.info("OPENAI_API_KEY not set; falling back to Mistral")
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=settings.MISTRAL_API_BASE or "https://api.mistral.ai/v1",
            )
            model = settings.MISTRAL_MODEL or "mistral-small-latest"
            return LLMClientInfo(client=client, model=model, provider="mistral")

    return None
