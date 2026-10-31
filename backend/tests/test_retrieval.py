import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.db.database import AsyncSessionLocal
from app.services.connectors import DriveItem
from app.services.indexing import index_file
from app.services.query_engine import (
    CONVERSATION,
    DOCUMENT_QUESTION,
    LIST_DOCUMENTS,
    SUMMARIZE,
    CatalogEntry,
    date_range,
    plan_query,
)
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


# --- Document lists: "how many documents are about AI", "which files are from today" ---------------------

KOLKATA = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 25, 0, 30, tzinfo=KOLKATA)  # still 24 October in UTC: dates must be the user's


@pytest.mark.parametrize("query,intent,topic,dates", [
    ("How many documents are about AI?", LIST_DOCUMENTS, "AI", None),
    ("how many document are of today", LIST_DOCUMENTS, None, "modified or added on 2026-10-25"),
    ("Which files mention Contoso?", LIST_DOCUMENTS, "Contoso", None),
    ("How many AI documents do I have?", LIST_DOCUMENTS, "AI", None),
    ("List all HR-related documents", LIST_DOCUMENTS, "HR", None),
    ("Which documents do I have about pricing?", LIST_DOCUMENTS, "pricing", None),
    ("Which documents mention version 4.2.1?", LIST_DOCUMENTS, "version 4.2.1", None),
    ("how many pdfs about kubernetes from the past 3 days", LIST_DOCUMENTS, "kubernetes",
     "modified or added between 2026-10-23 and 2026-10-25"),
    ("Which files were modified this week?", LIST_DOCUMENTS, None, "modified between 2026-10-19 and 2026-10-25"),
    ("show me documents uploaded last month", LIST_DOCUMENTS, None, "added between 2026-09-01 and 2026-09-30"),
    ("what did I upload today?", LIST_DOCUMENTS, None, "added on 2026-10-25"),
    ("documents indexed yesterday", LIST_DOCUMENTS, None, "indexed on 2026-10-24"),
    ("Which documents do I have?", LIST_DOCUMENTS, None, None),
    ("How many documents must be submitted for a claim?", DOCUMENT_QUESTION, None, None),
    ("What do the documents about pricing say about discounts?", DOCUMENT_QUESTION, None, None),
    ("What does the document about pricing say?", DOCUMENT_QUESTION, None, None),
    ("Which documents are due today?", DOCUMENT_QUESTION, None, None),
    ("Summarize all documents about AI", DOCUMENT_QUESTION, None, None),
])
async def test_document_list_questions_get_a_topic_and_dates(query, intent, topic, dates):
    plan = await plan_query(query, [], [], now=NOW)
    assert (plan.intent, plan.topic, plan.date_range.describe() if plan.date_range else None) == (intent, topic, dates)


@pytest.mark.parametrize("query,expected", [
    ("files from 20 October 2026", "on 2026-10-20"),
    ("files dated 2026-10-20", "on 2026-10-20"),
    ("documents from 20/10/2026", "on 2026-10-20"),
    ("documents from 10/20/2026", "on 2026-10-20"),
    ("documents from Oct 20", "on 2026-10-20"),
    ("documents from October 20th, 2026", "on 2026-10-20"),
    ("which documents are from December", "between 2025-12-01 and 2025-12-31"),  # the last December
    ("documents from October 2026", "between 2026-10-01 and 2026-10-31"),
    ("how many documents in 2025", "between 2025-01-01 and 2025-12-31"),
    ("documents from last year", "between 2025-01-01 and 2025-12-31"),
    ("documents from last week", "between 2026-10-12 and 2026-10-18"),
    ("files from this month", "between 2026-10-01 and 2026-10-25"),
    ("documents from 31/02/2026", None),
    ("the Budget 2026 workbook", None),
])
def test_date_expressions(query, expected):
    found = date_range(query, NOW)
    assert (found.describe().removeprefix("modified or added ") if found else None) == expected


async def test_same_date_as_a_document_uses_the_users_local_date():
    evening_utc = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)  # 21 October, 01:30 in Kolkata
    catalog = [CatalogEntry("d1", "Budget 2026.xlsx", "xlsx", "/", evening_utc, "onedrive")]
    question = "Which documents have the same date as Budget 2026.xlsx?"
    in_kolkata = await plan_query(question, [], catalog, now=NOW)
    in_utc = await plan_query(question, [], catalog, now=NOW.astimezone(UTC))
    assert in_kolkata.intent == LIST_DOCUMENTS
    assert in_kolkata.date_range.describe() == "modified or added on 2026-10-21"
    assert in_utc.date_range.describe() == "modified or added on 2026-10-20"


async def test_follow_up_document_lists_take_filters_from_the_rewritten_question(monkeypatch):
    from app.services import query_engine

    async def rewrite(*args):
        return {"standalone_query": "Which documents about AI are from today?", "intent": "list_documents"}

    monkeypatch.setattr(query_engine, "_llm_rewrite", rewrite)
    history = [{"role": "user", "content": "How many documents are about AI?"}, {"role": "assistant", "content": "Two [1][2]"}]
    plan = await plan_query("and which of them are from today?", history, [], now=NOW)
    assert (plan.intent, plan.topic, plan.date_range.describe()) == (LIST_DOCUMENTS, "AI", "modified or added on 2026-10-25")


async def _index_dated(user_id: str, name: str, text: str, modified: datetime, created=None):
    item = DriveItem(id=f"dated-{name}", name=name, is_folder=False, drive_type="onedrive", mime_type="text/plain",
                     modified_date=modified, created_date=created, web_url=f"https://example.test/{name}")
    assert (await index_file(user_id, item, text.encode())).status == "INDEXED"


async def test_date_lists_are_exact_and_every_document_is_linked():
    user, _ = await make_user("dates@example.com")
    now = datetime.now(KOLKATA)
    await _index_dated(user.id, "Today Notes.txt", "Sprint planning notes for the platform team.", now)
    await _index_dated(user.id, "Yesterday Notes.txt", "Retrospective notes.", now - timedelta(days=1))
    await _index_dated(user.id, "Old Notes.txt", "Kick-off notes.", now - timedelta(days=40))
    await _index_dated(user.id, "Uploaded Copy.txt", "A scanned contract copy.", now - timedelta(days=100), created=now)
    today = now.date().isoformat()

    async def listed(question):
        async with AsyncSessionLocal() as db:
            prepared = await prepare(db, user.id, question, [], tz=KOLKATA)
        assert prepared.plan.intent == LIST_DOCUMENTS and not prepared.chunks
        return prepared.messages[-1]["content"], sorted(c["filename"] for c in prepared.citations), prepared.citations

    content, names, citations = await listed("How many documents are from today?")
    assert f'<catalog count="2" filter="modified or added on {today}">' in content
    assert names == ["Today Notes.txt", "Uploaded Copy.txt"] and "<sources>" not in content
    assert all(c["url"].startswith("https://example.test/") for c in citations)
    assert (await listed("Which documents were modified today?"))[1] == ["Today Notes.txt"]
    assert (await listed("What did I upload today?"))[1] == ["Uploaded Copy.txt"]
    assert (await listed("documents from yesterday"))[1] == ["Yesterday Notes.txt"]
    content, names, _ = await listed("How many documents are from 1 January 2001?")
    assert '<catalog count="0"' in content and names == []


async def test_date_filters_use_the_users_time_zone():
    user, _ = await make_user("zones@example.com")
    await _index_dated(user.id, "Late Upload.txt", "Uploaded late in the evening (UTC).",
                       datetime(2026, 10, 20, 20, 0, tzinfo=UTC))  # 21 October in Kolkata
    question = "Which documents are from 21 October 2026?"
    async with AsyncSessionLocal() as db:
        in_kolkata = await prepare(db, user.id, question, [], tz=KOLKATA)
        in_utc = await prepare(db, user.id, question, [])
    assert [c["filename"] for c in in_kolkata.citations] == ["Late Upload.txt"]
    assert in_utc.citations == []


async def test_topic_lists_rank_every_document_by_its_best_passage(monkeypatch):
    from app.core.config import settings

    user, _ = await make_user("topics@example.com")
    now = datetime.now(UTC)
    await _index_dated(user.id, "AI Strategy.txt", "Our AI strategy covers model governance and AI safety reviews.", now)
    await _index_dated(user.id, "Model Risk.txt", "Every AI model release needs a risk review by the board.",
                       now - timedelta(days=40))
    await _index_dated(user.id, "Leave Policy.txt", "Employees receive 18 days of annual leave.", now)
    await _index_dated(user.id, "Cafeteria Menu.txt", "Lunch is served from noon and Friday is pizza day.", now)

    async with AsyncSessionLocal() as db:
        prepared = await prepare(db, user.id, "How many documents are about AI?", [])
        names = [c.filename for c in prepared.chunks]
        assert prepared.plan.topic == "AI" and len(names) == len(set(names)) == 4  # one passage per document
        assert set(names[:2]) == {"AI Strategy.txt", "Model Risk.txt"}
        assert '<inventory topic="AI" documents="4" more="false"/>' in prepared.messages[-1]["content"]
        assert [c["source_id"] for c in prepared.citations] == [1, 2, 3, 4]

        prepared = await prepare(db, user.id, "Which documents about AI are from this week?", [])
        assert "Model Risk.txt" not in {c.filename for c in prepared.chunks}  # the date filter applies first
        assert prepared.chunks[0].filename == "AI Strategy.txt"

        monkeypatch.setattr(settings, "INVENTORY_MAX_DOCUMENTS", 1)
        prepared = await prepare(db, user.id, "How many documents are about AI?", [])
        assert len(prepared.chunks) == 1 and 'more="true"' in prepared.messages[-1]["content"]


async def test_list_filters_ignore_file_names_read_both_date_cues_and_folders():
    catalog = [
        CatalogEntry("d1", "Strategy 2027.pptx", "pptx", "/Corpus", datetime(2026, 10, 24, 20, 30, tzinfo=UTC), "onedrive"),
        CatalogEntry("d2", "Leave Policy 2026.pdf", "pdf", "/HR", datetime(2026, 9, 1, 9, tzinfo=UTC), "onedrive"),
        CatalogEntry("d3", "Budget 2026.xlsx", "xlsx", "/Finance/2026", datetime(2026, 9, 1, 9, tzinfo=UTC), "onedrive"),
    ]
    # The extension of the named document is not a file-type filter.
    plan = await plan_query("Show me the documents from the same date as Strategy 2027.pptx", [], catalog, now=NOW)
    assert (plan.intent, plan.file_types) == (LIST_DOCUMENTS, [])
    assert plan.date_range.describe() == "modified or added on 2026-10-25"
    # A follow-up that repeats the answer's own wording keeps "modified or added", not "added" only.
    plan = await plan_query("Which of the documents modified or added on 2026-10-25 are presentations?", [], catalog, now=NOW)
    assert (plan.file_types, plan.date_range.describe()) == (["pptx"], "modified or added on 2026-10-25")
    # "... mentioning Kubernetes are PDFs": the topic stops before the file-type clause.
    plan = await plan_query("Which of the documents mentioning Kubernetes are PDFs?", [], catalog, now=NOW)
    assert (plan.topic, plan.topic_mode, plan.file_types) == ("Kubernetes", "mentions", ["pdf"])
    plan = await plan_query("Which documents are in the finance folder?", [], catalog, now=NOW)
    assert (plan.intent, plan.folder, plan.topic) == (LIST_DOCUMENTS, "Finance", None)
    plan = await plan_query("How many PDFs in the HR folder are about leave?", [], catalog, now=NOW)
    assert (plan.folder, plan.topic, plan.topic_mode, plan.file_types) == ("HR", "leave", "about", ["pdf"])
