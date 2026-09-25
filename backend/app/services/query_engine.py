import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.telemetry.langfuse_client import get_prompt_template, observe_rag

logger = logging.getLogger(__name__)


@dataclass
class QueryIntent:
    """Structured output from query understanding layer."""
    original_query: str
    semantic_query: str
    question_type: str  # FACTUAL, DOCUMENT_SEARCH, SUMMARY, COMPARISON, METADATA_SEARCH, RECENT_CHANGES, UNKNOWN
    folder_filter: Optional[str] = None
    file_type_filter: Optional[str] = None
    filename_filter: Optional[str] = None
    modified_after: Optional[datetime] = None
    modified_before: Optional[datetime] = None
    target_documents: List[str] = None


class QueryUnderstandingEngine:
    """
    Analyzes user queries to classify intent and extract metadata filters
    (folder paths, date ranges, file types, document names) for hybrid retrieval.
    """

    def __init__(self):
        self.known_folders = ["/HR", "/Finance", "/Engineering", "/Company"]
        self.known_types = ["pdf", "docx", "xlsx", "pptx", "txt"]

    @observe_rag(name="query_understanding", prompt_name="query_classifier", prompt_version="v1")
    async def analyze_query(self, query: str) -> QueryIntent:
        clean_q = query.strip()
        lower_q = clean_q.lower()

        # 1. Rule-based Fast Metadata Detection (instant, zero cost, 100% reliable)
        folder_filter = None
        for f in self.known_folders:
            folder_name = f.replace("/", "").lower()
            if folder_name in lower_q:
                folder_filter = f
                break

        file_type_filter = None
        for t in self.known_types:
            if f".{t}" in lower_q or f"{t} file" in lower_q or f"{t} document" in lower_q:
                file_type_filter = t
                break

        # Time-based filter detection
        modified_after = None
        now = datetime.now(timezone.utc)
        if "this month" in lower_q:
            modified_after = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        elif "this year" in lower_q:
            modified_after = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        elif "recently" in lower_q or "recent" in lower_q or "last 30 days" in lower_q:
            modified_after = now - timedelta(days=30)
        elif "last week" in lower_q:
            modified_after = now - timedelta(days=7)

        # Question Type Classification
        if "compare" in lower_q or "difference between" in lower_q:
            question_type = "COMPARISON"
        elif "summarize" in lower_q or "summary of" in lower_q:
            question_type = "SUMMARY"
        elif "what changed" in lower_q or "recent changes" in lower_q or "modified" in lower_q:
            question_type = "RECENT_CHANGES"
        elif "what documents" in lower_q or "show documents" in lower_q or "list documents" in lower_q or "which documents" in lower_q:
            question_type = "METADATA_SEARCH"
        elif "where is" in lower_q or "find the document" in lower_q:
            question_type = "DOCUMENT_SEARCH"
        else:
            question_type = "FACTUAL"

        # If LLM API is available and query is complex, we can use LLM structured output
        if settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("your-"):
            try:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
                system_prompt = get_prompt_template("query_classifier", version="v1")
                res = await client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Extract metadata and intent for query: '{clean_q}'"},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                )
                llm_parsed = json.loads(res.choices[0].message.content or "{}")
                if llm_parsed.get("folder_path"):
                    folder_filter = llm_parsed["folder_path"]
                if llm_parsed.get("file_type"):
                    file_type_filter = llm_parsed["file_type"]
                if llm_parsed.get("question_type"):
                    question_type = llm_parsed["question_type"]
            except Exception as e:
                logger.debug(f"LLM query understanding fallback to rule engine: {e}")

        # Semantic query cleans out conversational filler
        semantic_q = clean_q
        for prefix in ["what is the ", "what are the ", "can you tell me ", "show me "]:
            if semantic_q.lower().startswith(prefix):
                semantic_q = semantic_q[len(prefix):]
                break

        return QueryIntent(
            original_query=clean_q,
            semantic_query=semantic_q,
            question_type=question_type,
            folder_filter=folder_filter,
            file_type_filter=file_type_filter,
            filename_filter=None,
            modified_after=modified_after,
            target_documents=[],
        )


query_engine = QueryUnderstandingEngine()
