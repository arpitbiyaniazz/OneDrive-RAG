import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.models.user import User
from app.services.sync_service import sync_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["Incremental Sync"])


class SyncStartResponse(BaseModel):
    status: str
    message: str
    is_syncing: bool
    summary: Optional[dict] = None


@router.post("/start", response_model=SyncStartResponse)
async def trigger_sync(
    background_tasks: BackgroundTasks,
    background: bool = Query(True, description="Whether to run sync asynchronously in the background"),
    current_user: User = Depends(get_current_user),
):
    """
    Triggers an incremental synchronization between Microsoft OneDrive and pgvector.
    Detects new, modified, unchanged, and deleted documents.
    """
    if sync_service.is_syncing:
        return SyncStartResponse(
            status="already_running",
            message="Incremental synchronization is already in progress.",
            is_syncing=True,
            summary=sync_service.get_last_result(),
        )

    if background:
        background_tasks.add_task(sync_service.run_sync, user_id=current_user.id)
        return SyncStartResponse(
            status="started",
            message="Incremental synchronization started in background.",
            is_syncing=True,
        )
    else:
        result = await sync_service.run_sync(user_id=current_user.id)
        return SyncStartResponse(
            status="completed",
            message="Incremental synchronization completed.",
            is_syncing=False,
            summary=result.to_dict(),
        )


@router.get("/status")
async def get_sync_status(
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves the current synchronization status and the latest sync summary.
    """
    return {
        "user_id": current_user.id,
        "is_syncing": sync_service.is_syncing,
        "last_result": sync_service.get_last_result(),
    }


@router.get("/delta")
async def get_delta_changes(
    delta_token: Optional[str] = Query(None, description="Previous @odata.deltaLink or delta token"),
    current_user: User = Depends(get_current_user),
):
    """
    Queries Microsoft Graph delta API (Section 23) to track changed, new, or deleted OneDrive items.
    """
    from app.services.microsoft_graph import get_onedrive_service
    service = get_onedrive_service()
    data = await service.get_delta_changes(access_token="", delta_token_or_url=delta_token)
    return data
