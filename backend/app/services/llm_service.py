import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    pass


def _client_class():
    if settings.LANGFUSE_ENABLED and settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
        try:
            from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI  # traces every completion

            return LangfuseAsyncOpenAI
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning("Langfuse OpenAI wrapper unavailable: %s", exc)
    return AsyncOpenAI


@dataclass
class LLMClient:
    client: Optional[AsyncOpenAI]
    model: str
    provider: str

    async def stream(self, messages: List[Dict[str, str]], **kwargs) -> AsyncIterator[str]:
        if self.provider == "mock":
            async for token in _mock_stream(messages):
                yield token
            return
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=kwargs.pop("temperature", settings.LLM_TEMPERATURE),
            max_tokens=kwargs.pop("max_tokens", settings.LLM_MAX_TOKENS),
            **kwargs,
        )
        async for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def complete_json(self, messages: List[Dict[str, str]], request_timeout: float, **kwargs) -> str:
        if self.provider == "mock":
            return "{}"
        res = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
            timeout=request_timeout,
            **kwargs,
        )
        return res.choices[0].message.content or "{}"


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    provider = settings.LLM_PROVIDER
    if provider == "mock":
        return LLMClient(client=None, model="mock-extractive", provider="mock")
    cls = _client_class()
    if provider == "mistral":
        if not settings.MISTRAL_API_KEY:
            raise LLMUnavailableError("LLM_PROVIDER=mistral requires MISTRAL_API_KEY")
        client = cls(api_key=settings.MISTRAL_API_KEY, base_url=settings.MISTRAL_API_BASE,
                     timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=2)
        return LLMClient(client=client, model=settings.MISTRAL_MODEL, provider="mistral")
    if not settings.OPENAI_API_KEY:
        raise LLMUnavailableError("LLM_PROVIDER=openai requires OPENAI_API_KEY")
    client = cls(api_key=settings.OPENAI_API_KEY, base_url=settings.OPENAI_API_BASE,
                 timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=2)
    return LLMClient(client=client, model=settings.OPENAI_MODEL, provider="openai")


_SOURCE_RE = re.compile(r'<source id="(\d+)"[^>]*>\n(.*?)\n</source>', re.DOTALL)


async def _mock_stream(messages: List[Dict[str, str]]) -> AsyncIterator[str]:
    """Deterministic extractive responder for tests (LLM_PROVIDER=mock, never allowed in production)."""
    content = messages[-1]["content"]
    question = content.rsplit("<question>", 1)[-1]
    q_words = set(re.findall(r"\w+", question.lower()))
    best, best_score = None, 0
    for source_id, passage in _SOURCE_RE.findall(content):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", passage):
            score = len(q_words & set(re.findall(r"\w+", sentence.lower())))
            if sentence.strip() and score > best_score:
                best, best_score = (source_id, sentence.strip()), score
    text = f"{best[1]} [{best[0]}]" if best else "I couldn't find this in your connected documents."
    for word in text.split(" "):
        yield word + " "
