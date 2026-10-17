import json

from app.services import llm_service, rag_service
from app.services.jobs import drain_jobs


def _events(body: str):
    return [json.loads(line[6:]) for line in body.split("\n\n") if line.startswith("data: ")]


async def _index(client, headers, file_id="file_hr_leave"):
    await client.post("/api/ingestion/start", headers=headers, json={"items": [{"id": file_id, "drive_type": "onedrive"}]})
    await drain_jobs()


async def test_stream_contract_citations_and_persistence(client, auth_headers):
    await _index(client, auth_headers)
    res = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "How many annual leave days do employees get?"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = _events(res.text)
    assert events[0]["status"] == "searching" and events[1]["status"] == "generating"
    done = events[-1]
    assert done["done"] is True and done["message_id"]
    answer = "".join(e.get("token", "") for e in events)
    assert "18 annual leave days" in answer and "[1]" in answer
    cite = done["citations"][0]
    assert cite["source_id"] == 1 and cite["filename"] == "Leave Policy 2026.pdf" and cite["page"] == 1
    assert cite["url"].startswith("https://onedrive.live.com/")

    session_id = res.headers["X-Chat-Session-Id"]
    msgs = (await client.get(f"/api/chat/sessions/{session_id}/messages", headers=auth_headers)).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["id"] == done["message_id"] and msgs[1]["citations"][0]["filename"] == "Leave Policy 2026.pdf"

    fb = await client.post("/api/chat/feedback", headers=auth_headers, json={"message_id": done["message_id"], "feedback": 1})
    assert fb.status_code == 200
    msgs = (await client.get(f"/api/chat/sessions/{session_id}/messages", headers=auth_headers)).json()["messages"]
    assert msgs[1]["feedback"] == 1


async def test_follow_up_history_is_sent_once_without_stale_citations(client, auth_headers, monkeypatch):
    await _index(client, auth_headers)
    first = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "What is the annual leave allowance?"})
    session_id = first.headers["X-Chat-Session-Id"]
    captured = {}
    real_prepare = rag_service.prepare

    async def spy(db, user_id, question, history):
        prepared = await real_prepare(db, user_id, question, history)
        captured["messages"] = prepared.messages
        return prepared

    monkeypatch.setattr(rag_service, "prepare", spy)
    await client.post("/api/chat/stream", headers=auth_headers, json={"query": "And sick leave?", "session_id": session_id})
    messages = captured["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert "And sick leave?" in messages[-1]["content"] and all("And sick leave?" not in m["content"] for m in messages[:-1])
    assert "[1]" not in messages[2]["content"]


async def test_llm_failure_returns_an_error_event_not_a_fabricated_answer(client, auth_headers, monkeypatch):
    await _index(client, auth_headers)

    async def broken(self, messages, **kwargs):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    monkeypatch.setattr(llm_service.LLMClient, "stream", broken)
    res = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "What is the leave policy?"})
    events = _events(res.text)
    assert "error" in events[-1] and events[-1]["done"] is True
    assert not any("token" in e for e in events)
    session_id = res.headers["X-Chat-Session-Id"]
    msgs = (await client.get(f"/api/chat/sessions/{session_id}/messages", headers=auth_headers)).json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]


async def test_greeting_skips_retrieval(client, auth_headers):
    await _index(client, auth_headers)
    events = _events((await client.post("/api/chat/stream", headers=auth_headers, json={"query": "hello"})).text)
    assert events[1]["intent"] == "conversation" and events[1]["sources"] == 0


async def test_input_validation(client, auth_headers):
    assert (await client.post("/api/chat/stream", headers=auth_headers, json={"query": "   "})).status_code == 422
    assert (await client.post("/api/chat/stream", headers=auth_headers, json={"query": "x" * 5000})).status_code == 422
    res = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "hi", "session_id": "missing"})
    assert res.status_code == 404


async def test_sessions_list_and_delete(client, auth_headers):
    res = await client.post("/api/chat/stream", headers=auth_headers, json={"query": "hello there"})
    session_id = res.headers["X-Chat-Session-Id"]
    sessions = (await client.get("/api/chat/sessions", headers=auth_headers)).json()["sessions"]
    assert [s["id"] for s in sessions] == [session_id]
    assert (await client.delete(f"/api/chat/sessions/{session_id}", headers=auth_headers)).status_code == 200
    assert (await client.get("/api/chat/sessions", headers=auth_headers)).json()["sessions"] == []
