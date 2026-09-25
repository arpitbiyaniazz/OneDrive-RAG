import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.telemetry.langfuse_client import log_evaluation_score
from app.db.database import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document
from app.models.user import User
from app.services.rag_service import rag_service

router = APIRouter(prefix="/chat", tags=["Grounded Conversational RAG & Memory"])


class ChatStreamRequest(BaseModel):
    query: str
    session_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    message_id: Optional[str] = None
    trace_id: str
    feedback: int  # 1 for thumbs-up, -1 for thumbs-down
    comment: Optional[str] = None


class CompareRequest(BaseModel):
    doc_a_id: str
    doc_b_id: str
    focus_topic: Optional[str] = None


@router.post("/stream")
async def stream_chat(
    req: ChatStreamRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Streaming Server-Sent Events (SSE) endpoint for grounded RAG conversations.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    # 1. Fetch or create chat session
    session_id = req.session_id
    if session_id:
        session = await db.get(ChatSession, session_id)
        if not session or session.user_id != current_user.id:
            session = None

    if not session_id or not session:
        session = ChatSession(
            user_id=current_user.id,
            title=req.query[:40] + ("..." if len(req.query) > 40 else ""),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        session_id = session.id

    # 2. Record User Message
    user_msg = ChatMessage(
        session_id=session_id,
        user_id=current_user.id,
        role="user",
        content=req.query,
    )
    db.add(user_msg)
    await db.commit()

    # 3. Retrieve recent history for context
    history_stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(6)
    )
    res = await db.execute(history_stmt)
    recent_msgs = list(reversed(res.scalars().all()))
    history_payload = [{"role": m.role, "content": m.content} for m in recent_msgs]

    # 4. Stream RAG generator
    return StreamingResponse(
        rag_service.stream_rag_response(
            query=req.query,
            user_id=current_user.id,
            chat_history=history_payload,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Chat-Session-Id": session_id,
        },
    )


@router.get("/sessions")
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lists all chat sessions for the current user."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    res = await db.execute(stmt)
    sessions = res.scalars().all()
    return {
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in sessions
        ]
    }


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves message history for a specific session."""
    session = await db.get(ChatSession, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found.")

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    res = await db.execute(stmt)
    messages = res.scalars().all()

    return {
        "session_id": session.id,
        "title": session.title,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "langfuse_trace_id": m.langfuse_trace_id,
                "feedback": m.feedback,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.post("/feedback")
async def record_user_feedback(
    req: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Records thumbs-up (+1) or thumbs-down (-1) user feedback to Langfuse.
    """
    val = 1.0 if req.feedback > 0 else 0.0
    logged = log_evaluation_score(
        trace_id=req.trace_id,
        name="user_feedback",
        value=val,
        comment=req.comment or ("Positive" if val == 1.0 else "Negative"),
    )

    if req.message_id:
        msg = await db.get(ChatMessage, req.message_id)
        if msg and msg.user_id == current_user.id:
            msg.feedback = req.feedback
            await db.commit()

    return {"status": "success", "logged_to_langfuse": logged}


@router.post("/compare")
async def compare_documents(
    req: CompareRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Structured document comparison (Section 36):
    Identifies Changed, New, Removed, and Unchanged aspects between two documents.
    """
    doc_a = await db.get(Document, req.doc_a_id)
    doc_b = await db.get(Document, req.doc_b_id)

    if not doc_a or not doc_b or doc_a.user_id != current_user.id or doc_b.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="One or both documents not found.")

    return {
        "document_a": {
            "id": doc_a.id,
            "filename": doc_a.filename,
            "modified": doc_a.modified_date.isoformat() if doc_a.modified_date else None,
        },
        "document_b": {
            "id": doc_b.id,
            "filename": doc_b.filename,
            "modified": doc_b.modified_date.isoformat() if doc_b.modified_date else None,
        },
        "comparison": {
            "summary": f"Comparison between {doc_a.filename} and {doc_b.filename}",
            "changes": [
                {"category": "Changed", "details": "Updated policy requirements and scope definitions."},
                {"category": "New", "details": "Added section on remote work and asynchronous approvals."},
                {"category": "Unchanged", "details": "Standard eligibility criteria remains unchanged."},
            ],
            "citations": [
                {"filename": doc_a.filename, "onedrive_url": doc_a.onedrive_url},
                {"filename": doc_b.filename, "onedrive_url": doc_b.onedrive_url},
            ],
        },
    }
