import re
from datetime import UTC, datetime

import pytest

from app.db.database import AsyncSessionLocal
from app.services.connectors import DriveItem
from app.services.indexing import index_file
from app.services.query_engine import CONVERSATION, DOCUMENT_QUESTION, LIST_DOCUMENTS, SUMMARIZE, plan_query
from app.services.rag_service import prepare
from app.services.retrieval_service import hybrid_search, load_catalog
from tests.conftest import make_user
from tests.fixtures.corpus import build_corpus

CORPUS = [f for f in build_corpus() if f.should_index]


def _norm(text):
    return re.sub(r"\s+", " ", text).lower()


async def _index_corpus():
    user, _ = await make_user("corpus@example.com")
    for i, f in enumerate(CORPUS):
        item = DriveItem(id=f"corpus-{i}", name=f.name, is_folder=False, drive_type="onedrive", mime_type=f.mime,
                         size=len(f.data), modified_date=datetime(2026, 9, 1, tzinfo=UTC), folder_path="/Corpus")
        result = await index_file(user.id, item, f.data)
        assert result.status == "INDEXED", (f.name, result)
    return user


async def test_every_fact_in_every_document_is_retrievable():
    user = await _index_corpus()
    misses = []
    async with AsyncSessionLocal() as db:
        catalog = await load_catalog(db, user.id)
        for f in CORPUS:
            for needle in f.needles:
                plan = await plan_query(needle.question, [], catalog)
                chunks = await hybrid_search(db, user.id, plan)
                if not any(_norm(needle.fact) in _norm(c.content) for c in chunks):
                    misses.append(f"{f.name}: {needle.question}")
    assert not misses, misses


async def test_named_document_questions_are_scoped_and_summaries_read_the_whole_document():
    user = await _index_corpus()
    async with AsyncSessionLocal() as db:
        prepared = await prepare(db, user.id, "Summarize the Employee Handbook", [])
        assert prepared.plan.intent == SUMMARIZE
        assert {c.filename for c in prepared.chunks} == {"Employee Handbook.docx"}
        assert [c.chunk_index for c in prepared.chunks] == sorted(c.chunk_index for c in prepared.chunks)

        prepared = await prepare(db, user.id, "In Strategy 2027, what is the churn target?", [])
        assert prepared.chunks[0].filename == "Strategy 2027.pptx"

        prepared = await prepare(db, user.id, "Which documents do I have?", [])
        assert prepared.plan.intent == LIST_DOCUMENTS and not prepared.chunks
        assert "<catalog" in prepared.messages[-1]["content"] and "Runbook.md" in prepared.messages[-1]["content"]


@pytest.mark.parametrize("query,intent", [
    ("hi", CONVERSATION),
    ("Thanks!", CONVERSATION),
    ("What can you do?", CONVERSATION),
    ("list my files", LIST_DOCUMENTS),
    ("What documents are indexed?", LIST_DOCUMENTS),
    ("What are the three pillars of our strategy?", DOCUMENT_QUESTION),
])
async def test_intent_rules(query, intent):
    plan = await plan_query(query, [], [])
    assert plan.intent == intent
    assert plan.target_document_ids == [] and plan.file_types == []  # no accidental filters ('three' != 'HR')


async def test_time_words_only_filter_with_an_explicit_cue():
    assert (await plan_query("What is our most recent strategy?", [], [])).modified_after is None
    assert (await plan_query("Which files were updated this month?", [], [])).modified_after is not None


async def test_follow_up_without_llm_keeps_the_question():
    history = [{"role": "user", "content": "What is the leave policy?"}, {"role": "assistant", "content": "18 days [1]"}]
    plan = await plan_query("and sick leave?", history, [])
    assert plan.search_query == "and sick leave?"  # mock LLM returns {}: graceful fallback
