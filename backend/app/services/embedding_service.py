import hashlib
import logging
import math
from abc import ABC, abstractmethod
from typing import List
import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract interface for generating vector embeddings."""

    @abstractmethod
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        pass

    @abstractmethod
    async def embed_query(self, query: str) -> List[float]:
        pass


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Generates embeddings using OpenAI API (e.g. text-embedding-3-small)."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # Batch in chunks of 64
        batch_size = 64
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = [t[:8000] for t in texts[i:i + batch_size]]  # Token guard
            res = await self.client.embeddings.create(input=batch, model=self.model)
            all_embeddings.extend([d.embedding for d in res.data])

        return all_embeddings

    async def embed_query(self, query: str) -> List[float]:
        res = await self.client.embeddings.create(input=[query[:8000]], model=self.model)
        return res.data[0].embedding


class DeterministicMockEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic mock embedding provider for tests and offline development.
    Generates a unit-normalized vector of 1536 dimensions.
    """

    def __init__(self, dimension: int = 1536):
        self.dimension = dimension

    def _generate_vector(self, text: str) -> List[float]:
        # Hash text to generate a reproducible pseudo-random seed
        seed_int = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = np.random.default_rng(seed_int)
        vec = rng.standard_normal(self.dimension)
        # Normalize to unit length
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._generate_vector(t) for t in texts]

    async def embed_query(self, query: str) -> List[float]:
        return self._generate_vector(query)


def get_embedding_provider() -> EmbeddingProvider:
    """Returns OpenAI provider if API key configured, otherwise deterministic mock provider."""
    if settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("your-"):
        return OpenAIEmbeddingProvider(
            api_key=settings.OPENAI_API_KEY,
            model=settings.EMBEDDING_MODEL,
        )
    return DeterministicMockEmbeddingProvider(dimension=settings.EMBEDDING_DIMENSION)
