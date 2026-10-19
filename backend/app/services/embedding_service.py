import asyncio
import hashlib
import logging
import math
import re
from functools import lru_cache
from typing import List, Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bumped whenever parsing/chunking changes in a way that should trigger re-indexing.
PIPELINE_VERSION = "pipeline-4"
_MAX_EMBED_INPUT_CHARS = 8000
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


# Mistral rejects embedding requests above ~16k tokens in total; stay well below with an estimate
# (its tokenizer spends about one token per digit, so spreadsheets are much denser than prose).
_MAX_BATCH_TOKENS = 10000


class EmbeddingError(Exception):
    pass


class _BatchTooLarge(Exception):
    pass


def estimate_tokens(text: str) -> int:
    digits = sum(ch.isdigit() for ch in text)
    return digits + (len(text) - digits) // 3 + 1


class EmbeddingProvider(Protocol):
    model_id: str

    async def embed_documents(self, texts: List[str]) -> List[List[float]]: ...

    async def embed_query(self, text: str) -> List[float]: ...


class OpenAICompatibleEmbeddings:
    """Embeddings over the OpenAI-compatible API (OpenAI or Mistral's /v1/embeddings)."""

    def __init__(self, api_key: str, model: str, base_url: str | None, provider: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0)
        self.model = model
        self.model_id = f"{provider}:{model}:{settings.EMBEDDING_DIMENSION}"

    async def _embed(self, batch: List[str]) -> List[List[float]]:
        delay = 2.0
        for attempt in range(6):
            try:
                # encoding_format="float": the SDK defaults to base64, which not every provider honours.
                res = await self.client.embeddings.create(model=self.model, input=batch, encoding_format="float")
                vectors = [item.embedding for item in sorted(res.data, key=lambda d: d.index)]
                if vectors and len(vectors[0]) != settings.EMBEDDING_DIMENSION:
                    raise EmbeddingError(
                        f"{self.model} returned {len(vectors[0])}-dim vectors but EMBEDDING_DIMENSION="
                        f"{settings.EMBEDDING_DIMENSION}"
                    )
                return vectors
            except (RateLimitError, APIConnectionError) as exc:
                retry = exc
            except APIStatusError as exc:
                if exc.status_code in (400, 413) and "token" in str(exc.message).lower() and len(batch) > 1:
                    raise _BatchTooLarge() from exc
                if exc.status_code < 500:
                    raise EmbeddingError(f"Embedding request rejected ({exc.status_code}): {exc.message}") from exc
                retry = exc
            if attempt == 5:
                raise EmbeddingError(f"Embedding request failed after retries: {retry}") from retry
            logger.warning("Embedding call failed (%s), retrying in %.0fs", type(retry).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
        raise EmbeddingError("unreachable")

    async def _embed_split(self, batch: List[str]) -> List[List[float]]:
        """Embeds a batch, halving it when the provider says it has too many tokens."""
        try:
            return await self._embed(batch)
        except _BatchTooLarge:
            middle = len(batch) // 2
            return await self._embed_split(batch[:middle]) + await self._embed_split(batch[middle:])

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        size = max(1, settings.EMBEDDING_BATCH_SIZE)
        batch: List[str] = []
        tokens = 0
        for text in (t[:_MAX_EMBED_INPUT_CHARS] for t in texts):
            estimate = estimate_tokens(text)
            if batch and (len(batch) >= size or tokens + estimate > _MAX_BATCH_TOKENS):
                out.extend(await self._embed_split(batch))
                batch, tokens = [], 0
            batch.append(text)
            tokens += estimate
        if batch:
            out.extend(await self._embed_split(batch))
        return out

    async def embed_query(self, text: str) -> List[float]:
        return (await self._embed([text[:_MAX_EMBED_INPUT_CHARS]]))[0]


class HashingEmbeddings:
    """Deterministic bag-of-words feature hashing. Test/offline use only: lexical, not semantic."""

    def __init__(self, dimension: int):
        self.dimension = dimension
        self.model_id = f"mock:hashing:{dimension}"

    def _vector(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            vec[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> List[float]:
        return self._vector(text)


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    provider = settings.EMBEDDING_PROVIDER
    if provider == "mistral":
        if not settings.MISTRAL_API_KEY:
            raise EmbeddingError("EMBEDDING_PROVIDER=mistral requires MISTRAL_API_KEY")
        return OpenAICompatibleEmbeddings(settings.MISTRAL_API_KEY, settings.EMBEDDING_MODEL, settings.MISTRAL_API_BASE, "mistral")
    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise EmbeddingError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAICompatibleEmbeddings(settings.OPENAI_API_KEY, settings.EMBEDDING_MODEL, settings.OPENAI_API_BASE, "openai")
    return HashingEmbeddings(settings.EMBEDDING_DIMENSION)


def index_version() -> str:
    """Fingerprint of the indexing pipeline; documents indexed with another version get re-indexed."""
    return f"{PIPELINE_VERSION}|{get_embedding_provider().model_id}"


def unsupported_version() -> str:
    """Fingerprint stored on files that could not be indexed: a parser upgrade or turning OCR on
    makes sync try them again."""
    return f"{PIPELINE_VERSION}|ocr:{settings.OCR_PROVIDER}"
