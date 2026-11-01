import asyncio
import html
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

    async def complete(self, messages: List[Dict[str, str]], max_tokens: int, request_timeout: float) -> str:
        """A whole (non-streamed) completion, e.g. the notes for one part of a long document."""
        if self.provider == "mock":
            return _mock_notes(messages[-1]["content"])
        res = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=max_tokens,
            timeout=request_timeout,
        )
        return res.choices[0].message.content or ""

    async def check(self) -> None:
        """Raises when the provider rejects the credentials (used by startup checks and `app.cli doctor`)."""
        if self.provider != "mock":
            await self.client.models.list()

    async def complete_json(self, messages: List[Dict[str, str]], request_timeout: float, **kwargs) -> str:
        if self.provider == "mock":
            return "{}"
        # No SDK retries: the caller has a fallback, and retries would multiply the deadline.
        res = await asyncio.wait_for(
            self.client.with_options(max_retries=0).chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=400,
                response_format={"type": "json_object"},
                timeout=request_timeout,
                **kwargs,
            ),
            timeout=request_timeout + 1,
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
_NOTE_RE = re.compile(r"^- (.+?) \[(\d+)\]$", re.MULTILINE)
_CATALOG_RE = re.compile(r'<catalog count="(\d+)"[^>]*>\n(.*?)</catalog>', re.DOTALL)
_CATALOG_ENTRY_RE = re.compile(r"^\[(\d+)\] (.+?) \(", re.MULTILINE)
_SOURCE_TITLE_RE = re.compile(r'<source id="(\d+)" title="([^"]*)"')


def _mock_document_list(content: str) -> Optional[str]:
    """A document list the way the answer prompt asks for one: the count, then '- Title [n]' per document."""
    catalog = _CATALOG_RE.search(content)
    if catalog:
        entries = _CATALOG_ENTRY_RE.findall(catalog.group(2))
        lines = [f"- {title} [{number}]" for number, title in entries]
        return "\n".join([f"{catalog.group(1)} documents match.", *lines]) if entries else "No documents match."
    if "<inventory " in content:
        lines = [f"- {html.unescape(title)} [{number}]" for number, title in _SOURCE_TITLE_RE.findall(content)]
        return "\n".join([f"{len(lines)} documents match.", *lines]) if lines else "No documents match."
    return None


def _mock_notes(content: str) -> str:
    """Mock notes for one part of a document: the first sentence of each passage."""
    lines = []
    for source_id, passage in _SOURCE_RE.findall(content):
        first = re.split(r"(?<=[.!?])\s+|\n+", passage.strip())[0].strip()
        if first:
            lines.append(f"- {first} [{source_id}]")
    return "\n".join(lines)


async def _mock_stream(messages: List[Dict[str, str]]) -> AsyncIterator[str]:
    """Deterministic extractive responder for tests (LLM_PROVIDER=mock, never allowed in production)."""
    content = messages[-1]["content"]
    listed = _mock_document_list(content)
    if listed is not None:
        for line in listed.split("\n"):
            yield line + "\n"
        return
    question = re.split(r"<question\b[^>]*>", content)[-1]
    q_words = set(re.findall(r"\w+", question.lower()))
    candidates = [(source_id, sentence)
                  for source_id, passage in _SOURCE_RE.findall(content)
                  for sentence in re.split(r"(?<=[.!?])\s+|\n+", passage)]
    candidates += [(source_id, note) for note, source_id in _NOTE_RE.findall(content)]
    best, best_score = None, 0
    for source_id, sentence in candidates:
        score = len(q_words & set(re.findall(r"\w+", sentence.lower())))
        if sentence.strip() and score > best_score:
            best, best_score = (source_id, sentence.strip()), score
    text = f"{best[1]} [{best[0]}]" if best else "I couldn't find this in your connected documents."
    for word in text.split(" "):
        yield word + " "
