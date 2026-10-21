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


@pytest.mark.parametrize("reply", [
    {"target_documents": True},
    {"target_documents": 3},
    {"target_documents": "Leave Policy 2026.pdf"},
    {"target_documents": [None, 7, {"x": 1}]},
    {"intent": ["summarize"]},
    {"intent": {"name": "summarize"}},
    {"standalone_query": 42, "intent": None, "target_documents": None},
])
async def test_malformed_rewrite_replies_are_ignored(monkeypatch, reply):
    from app.services import query_engine

    async def rewrite(*args):
        return reply

    monkeypatch.setattr(query_engine, "_llm_rewrite", rewrite)
    history = [{"role": "user", "content": "What is the leave policy?"}, {"role": "assistant", "content": "18 days [1]"}]
    plan = await plan_query("and sick leave?", history, [])
    assert plan.intent == DOCUMENT_QUESTION and plan.search_query == "and sick leave?" and plan.target_document_ids == []


async def test_long_documents_are_summarized_part_by_part(monkeypatch):
    from app.core.config import settings
    from app.services import rag_service
    from app.services.llm_service import LLMClient

    user, _ = await make_user("long@example.com")
    sections = [f"Section {n}. The milestone for phase {n} is code PH-{n:03d}, owned by team {n}. " + "Detail. " * 150
                for n in range(1, 41)]
    item = DriveItem(id="long-1", name="Annual Report.txt", is_folder=False, drive_type="onedrive", mime_type="text/plain",
                     modified_date=datetime(2026, 9, 1, tzinfo=UTC))
    assert (await index_file(user.id, item, "\n\n".join(sections).encode())).status == "INDEXED"
    monkeypatch.setattr(settings, "SUMMARY_MAX_CHARS", 12000)
    seen = []
    real_complete = LLMClient.complete

    async def spy(self, messages, max_tokens, request_timeout):
        seen.append(messages[-1]["content"])
        return await real_complete(self, messages, max_tokens, request_timeout)

    # Patch the class, not the cached instance: undoing an instance patch would leave a stale attribute.
    monkeypatch.setattr(LLMClient, "complete", spy)
    async with AsyncSessionLocal() as db:
        prepared = await prepare(db, user.id, "Summarize the Annual Report", [])
    assert prepared.plan.intent == SUMMARIZE and len(prepared.parts) > 1
    final = {}
    real_stream = LLMClient.stream

    async def capture(self, messages, **kwargs):
        final["messages"] = messages
        async for token in real_stream(self, messages, **kwargs):
            yield token

    monkeypatch.setattr(LLMClient, "stream", capture)
    "".join([t async for t in rag_service.stream_answer(prepared)])
    assert len(seen) == len(prepared.parts)
    everything = "\n".join(seen)
    assert "PH-001" in everything and "PH-040" in everything  # every part of the document was read
    notes = final["messages"][-1]["content"]
    assert notes.startswith('<notes title="Annual Report.txt"') and notes.count("<part n=") == len(prepared.parts)
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", notes)}
    assert cited and cited <= set(range(1, len(prepared.chunks) + 1)) and max(cited) > len(prepared.chunks) // 2


async def test_comparisons_get_passages_from_each_named_document():
    user = await _index_corpus()
    async with AsyncSessionLocal() as db:
        prepared = await prepare(db, user.id, "Compare Runbook and Strategy 2027", [])
    names = [c.filename for c in prepared.chunks]
    assert "Runbook.md" in names and "Strategy 2027.pptx" in names


async def test_loosely_named_and_latest_documents_are_found():
    user = await _index_corpus()
    async with AsyncSessionLocal() as db:
        catalog = await load_catalog(db, user.id)
        plan = await plan_query("Give me a summary of the handbook", [], catalog)
        assert plan.intent == SUMMARIZE
        assert [e.filename for e in catalog if e.id in plan.target_document_ids] == ["Employee Handbook.docx"]
        plan = await plan_query("Summarize my most recent document", [], catalog)
        assert plan.intent == SUMMARIZE and plan.target_document_ids == [catalog[0].id]


async def test_prompt_states_todays_date():
    from app.services.rag_service import build_messages

    system = build_messages("When is the next deadline?", [], [])[0]["content"]
    assert f"Today's date: {datetime.now(UTC).date().isoformat()}." in system
