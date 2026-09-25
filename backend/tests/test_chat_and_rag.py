import json
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.query_engine import query_engine


@pytest.mark.asyncio
async def test_query_engine_classification():
    # 1. Factual
    intent1 = await query_engine.analyze_query("What is the annual leave allowance?")
    assert intent1.question_type == "FACTUAL"

    # 2. Metadata folder & time
    intent2 = await query_engine.analyze_query("Show me Finance documents modified this month")
    assert intent2.folder_filter == "/Finance"
    assert intent2.modified_after is not None

    # 3. Comparison
    intent3 = await query_engine.analyze_query("Compare the 2025 and 2026 budget documents")
    assert intent3.question_type == "COMPARISON"


@pytest.mark.asyncio
async def test_grounded_rag_streaming_with_citations():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Ingest file first to ensure chunks exist
        await ac.post(
            "/api/ingestion/start",
            json={"item_ids": ["file_hr_leave"], "folder_path": "/HR"},
        )
        # Directly invoke stream
        stream_res = await ac.post(
            "/api/chat/stream",
            json={"query": "What is the leave policy?"},
        )
        assert stream_res.status_code == 200
        assert "text/event-stream" in stream_res.headers["content-type"]

        # Parse SSE stream chunks
        raw_body = stream_res.text
        lines = raw_body.strip().split("\n\n")
        tokens = []
        final_payload = None

        for line in lines:
            if line.startswith("data: "):
                data_str = line[6:]
                parsed = json.loads(data_str)
                if "token" in parsed:
                    tokens.append(parsed["token"])
                if parsed.get("done"):
                    final_payload = parsed

        full_answer = "".join(tokens)
        assert len(full_answer) > 0
        assert final_payload is not None
        assert "citations" in final_payload
        assert len(final_payload["citations"]) > 0
        citation = final_payload["citations"][0]
        assert "Leave Policy 2026.pdf" in citation["filename"]
        assert citation["page"] in [1, 2]
        assert "onedrive_url" in citation


@pytest.mark.asyncio
async def test_insufficient_information_unknown_query():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Ask about a topic not in the documents with a folder filter that has no matching items
        stream_res = await ac.post(
            "/api/chat/stream",
            json={"query": "What is the secret recipe for Martian space cookies?"},
        )
        assert stream_res.status_code == 200
        raw_body = stream_res.text
        assert "couldn't find enough information" in raw_body or "OneDrive" in raw_body


@pytest.mark.asyncio
async def test_chat_sessions_and_feedback():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # List sessions
        sessions_res = await ac.get("/api/chat/sessions")
        assert sessions_res.status_code == 200
        data = sessions_res.json()
        assert "sessions" in data
        assert len(data["sessions"]) > 0
        session_id = data["sessions"][0]["id"]

        # Get messages
        msg_res = await ac.get(f"/api/chat/sessions/{session_id}/messages")
        assert msg_res.status_code == 200
        messages = msg_res.json()["messages"]
        assert len(messages) > 0

        # Submit feedback
        fb_res = await ac.post(
            "/api/chat/feedback",
            json={
                "message_id": messages[0]["id"],
                "trace_id": "test_trace_123",
                "feedback": 1,
                "comment": "Accurate citations!",
            },
        )
        assert fb_res.status_code == 200
        assert fb_res.json()["status"] == "success"
