import logging
import re
from typing import List
from app.services.retrieval_service import RetrievedChunk

logger = logging.getLogger(__name__)


class RerankingService:
    """
    Reranks initial candidate chunks to optimize precision and contextual relevance.
    """

    def rerank(
        self,
        query: str,
        candidates: List[RetrievedChunk],
        top_k: int = 5,
    ) -> List[RetrievedChunk]:
        if not candidates or len(candidates) <= top_k:
            return candidates[:top_k]

        query_tokens = set(re.findall(r"\w+", query.lower()))
        scored_candidates = []

        for chunk in candidates:
            chunk_tokens = set(re.findall(r"\w+", chunk.content.lower()))
            overlap = len(query_tokens.intersection(chunk_tokens))
            overlap_score = overlap / max(1, len(query_tokens))

            # Combine vector similarity (60%) with lexical match precision (40%)
            hybrid_score = (chunk.similarity_score * 0.6) + (overlap_score * 0.4)
            scored_candidates.append((chunk, hybrid_score))

        # Sort descending by hybrid score
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in scored_candidates[:top_k]]


reranking_service = RerankingService()
