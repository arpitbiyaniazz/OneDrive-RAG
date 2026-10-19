import asyncio
import json
import logging
import uuid
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.telemetry.langfuse_client import log_score, rag_trace
from app.db.database import AsyncSessionLocal, get_db, utcnow
from app.models.chat import ChatMessage, ChatSession
from app.models.user import User
from app.services import rag_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatStreamRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=settings.MAX_QUERY_CHARS)
    session_id: Optional[str] = Field(None, max_length=36)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query cannot be empty.")
        return value.strip()


class FeedbackRequest(BaseModel):
    message_id: str = Field(..., max_length=36)
    feedback: int = Field(..., ge=-1, le=1)
    comment: Optional[str] = Field(None, max_length=2000)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _owned_session(db: AsyncSession, session_id: str, user: User) -> ChatSession:
    session = await db.get(ChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


async def _enforce_rate_limit(db: AsyncSession, user_id: str) -> None:
    """Per-user question limits (CHAT_RATE_LIMIT_PER_MINUTE / _PER_DAY) protect the LLM budget."""
    per_minute, per_day = settings.CHAT_RATE_LIMIT_PER_MINUTE, settings.CHAT_RATE_LIMIT_PER_DAY
    if per_minute <= 0 and per_day <= 0:
        return
    now = utcnow()
    minute_ago = now - timedelta(minutes=1)
    last_minute, last_day = (await db.execute(
        select(func.count().filter(ChatMessage.created_at > minute_ago), func.count())
        .where(ChatMessage.user_id == user_id, ChatMessage.role == "user",
               ChatMessage.created_at > now - timedelta(days=1))
    )).one()
    if per_minute > 0 and last_minute >= per_minute:
        raise HTTPException(status_code=429, detail="You are asking questions too quickly. Wait a minute and try again.",
                            headers={"Retry-After": "60"})
    if per_day > 0 and last_day >= per_day:
        raise HTTPException(status_code=429, detail="You have reached today's question limit. Try again tomorrow.",
                            headers={"Retry-After": "3600"})


@router.post("/stream")
async def stream_chat(req: ChatStreamRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Server-Sent Events: {"status"} -> {"token"}* -> {"done", citations, message_id} or {"error"}."""
    await _enforce_rate_limit(db, user.id)
    session = await _owned_session(db, req.session_id, user) if req.session_id else None
    history: List[dict] = []
    if session is None:
        title = req.query[:60] + ("..." if len(req.query) > 60 else "")
        session = ChatSession(user_id=user.id, title=title)
        db.add(session)
        await db.flush()
    else:
        rows = (await db.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(settings.CHAT_HISTORY_TURNS * 2)
        )).all()
        history = [{"role": r.role, "content": r.content} for r in reversed(rows)]
        if history and history[0]["role"] == "assistant":
            history = history[1:]  # start the window on a user turn
    db.add(ChatMessage(session_id=session.id, user_id=user.id, role="user", content=req.query))
    session.updated_at = utcnow()
    await db.commit()

    session_id, user_id, question = session.id, user.id, req.query
    message_id = str(uuid.uuid4())

    async def events():
        answer: List[str] = []
        citations: List[dict] = []
        trace_id = None
        error: Optional[str] = None
        try:
            yield _sse({"status": "searching", "session_id": session_id})
            with rag_trace("rag_chat", input={"question": question},
                           metadata={**rag_service.PROMPT_METADATA, "session_id": session_id}) as trace:
                trace_id = trace.trace_id
                async with AsyncSessionLocal() as work_db:
                    prepared = await rag_service.prepare(work_db, user_id, question, history)
                citations = prepared.citations
                # A long document is first read part by part ("reading"), then the answer is written.
                status = {"status": "reading", "parts": len(prepared.parts)} if prepared.parts else {"status": "generating"}
                yield _sse({**status, "intent": prepared.plan.intent, "sources": len(citations)})
                async for token in rag_service.stream_answer(prepared):
                    answer.append(token)
                    yield _sse({"token": token})
                trace.update(output="".join(answer), metadata={"intent": prepared.plan.intent,
                                                               "search_query": prepared.plan.search_query,
                                                               "sources": len(citations)})
        except (asyncio.CancelledError, GeneratorExit):
            error = "cancelled"  # client disconnected or pressed Stop
            raise
        except Exception:
            logger.exception("Chat generation failed")
            error = "The assistant could not answer right now. Please try again in a moment."
            yield _sse({"error": error, "done": True, "session_id": session_id, "message_id": message_id})
        finally:
            text = "".join(answer).strip()
            if error is None and not text:
                error = "The assistant returned an empty answer. Please try again."
                yield _sse({"error": error, "done": True, "session_id": session_id, "message_id": message_id})
            if error and text:
                text += "\n\n_(Answer interrupted.)_" if error == "cancelled" else "\n\n_(Answer incomplete because of an error.)_"
            if text:
                used = rag_service.cited_sources(text, citations)
                await asyncio.shield(_persist(session_id, user_id, message_id, text, used, trace_id))
                if error is None:
                    yield _sse({"done": True, "session_id": session_id, "message_id": message_id,
                                "citations": used, "trace_id": trace_id})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Chat-Session-Id": session_id},
    )


async def _persist(session_id: str, user_id: str, message_id: str, text: str, citations: List[dict], trace_id: Optional[str]) -> None:
    try:
        async with AsyncSessionLocal() as db:
            db.add(ChatMessage(id=message_id, session_id=session_id, user_id=user_id, role="assistant",
                               content=text, citations=citations, langfuse_trace_id=trace_id))
            session = await db.get(ChatSession, session_id)
            if session is not None:
                session.updated_at = utcnow()
            await db.commit()
    except Exception:
        logger.exception("Failed to persist assistant message")


@router.get("/sessions")
async def list_sessions(limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    rows = (await db.execute(
        select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(limit)
    )).scalars().all()
    return {"sessions": [
        {"id": s.id, "title": s.title,
         "created_at": s.created_at.isoformat() if s.created_at else None,
         "updated_at": s.updated_at.isoformat() if s.updated_at else None}
        for s in rows
    ]}


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    session = await _owned_session(db, session_id, user)
    rows = (await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at)
    )).scalars().all()
    return {
        "session_id": session.id,
        "title": session.title,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content, "citations": m.citations or [],
             "feedback": m.feedback, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in rows
        ],
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    session = await _owned_session(db, session_id, user)
    await db.execute(delete(ChatSession).where(ChatSession.id == session.id))
    await db.commit()
    return {"status": "deleted", "session_id": session_id}


@router.post("/feedback")
async def feedback(req: FeedbackRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    message = await db.get(ChatMessage, req.message_id)
    if message is None or message.user_id != user.id or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Message not found.")
    message.feedback = req.feedback or None
    await db.commit()
    logged = False
    if req.feedback:  # 0 clears the rating locally; it is not a negative score
        logged = log_score(message.langfuse_trace_id, "user_feedback", 1.0 if req.feedback > 0 else 0.0, req.comment,
                           score_id=f"feedback-{message.id}")
    return {"status": "recorded", "logged_to_langfuse": logged}
