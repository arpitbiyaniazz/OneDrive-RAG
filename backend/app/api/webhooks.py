import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.user import User
from app.services.sync_service import sync_service
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Microsoft Graph Webhooks & Change Notifications"])


class NotificationResourceData(BaseModel):
    id: Optional[str] = None


class ChangeNotificationItem(BaseModel):
    subscriptionId: Optional[str] = None
    clientState: Optional[str] = None
    changeType: Optional[str] = None  # created, updated, deleted
    resource: Optional[str] = None
    tenantId: Optional[str] = None
    resourceData: Optional[Dict[str, Any]] = None


class GraphNotificationPayload(BaseModel):
    value: List[ChangeNotificationItem] = []


@router.post("/onedrive")
async def onedrive_webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    validationToken: Optional[str] = Query(None, description="Handshake token sent by Microsoft Graph"),
):
    """
    Microsoft Graph Webhook notification endpoint (Section 23).
    1. Responds to Microsoft Graph subscription validation challenge with validationToken.
    2. Processes asynchronous change notifications (created, updated, deleted).
    3. Validates clientState secret token.
    4. Triggers incremental synchronization pipeline.
    """
    # 1. Validation challenge handshake
    if validationToken:
        logger.info("Received Microsoft Graph subscription validation challenge.")
        return PlainTextResponse(content=validationToken, status_code=200)

    # 2. Process incoming notifications
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    notifications = body.get("value", [])
    if not notifications:
        return Response(status_code=202)

    logger.info(f"Received {len(notifications)} Microsoft Graph change notification(s).")

    # 3. Verify clientState and queue sync
    valid_notifications = 0
    for notif in notifications:
        client_state = notif.get("clientState")
        # Validate clientState if configured
        if settings.WEBHOOK_CLIENT_STATE and client_state != settings.WEBHOOK_CLIENT_STATE:
            logger.warning("Rejected Microsoft Graph notification: invalid clientState token.")
            continue

        valid_notifications += 1
        change_type = notif.get("changeType", "unknown")
        resource = notif.get("resource", "me/drive")
        logger.info(f"Processing Graph change notification: changeType='{change_type}', resource='{resource}'")

    if valid_notifications > 0:
        # Resolve target user (in single-tenant or default to demo user for mock)
        async def _trigger_sync_job():
            async with AsyncSessionLocal() as session:
                stmt = select(User).limit(1)
                res = await session.execute(stmt)
                user = res.scalar_one_or_none()
                if user:
                    await sync_service.run_sync(user_id=user.id)

        background_tasks.add_task(_trigger_sync_job)

    return Response(
        content=f'{{"status":"accepted","processed_notifications":{valid_notifications}}}',
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.post("/gdrive")
async def gdrive_webhook_handler(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Google Drive Push Notification Webhook handler.
    Processes X-Goog-Resource-State headers (sync, add, update, trash, etc.).
    """
    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_state = request.headers.get("X-Goog-Resource-State", "unknown")
    channel_token = request.headers.get("X-Goog-Channel-Token")
    resource_id = request.headers.get("X-Goog-Resource-ID")

    if not channel_id:
        raise HTTPException(status_code=400, detail="Missing X-Goog-Channel-ID header.")

    # Validate channel token if configured
    if settings.GOOGLE_WEBHOOK_SECRET and channel_token and channel_token != settings.GOOGLE_WEBHOOK_SECRET:
        logger.warning("Rejected Google Drive notification: invalid channel token.")
        raise HTTPException(status_code=403, detail="Invalid channel token.")

    logger.info(f"Received Google Drive webhook: state='{resource_state}', channel='{channel_id}', resource='{resource_id}'")

    # Initial sync handshake from Google
    if resource_state == "sync":
        return Response(status_code=status.HTTP_200_OK, content='{"status":"sync_acknowledged"}', media_type="application/json")

    # For change events (add, update, trash), queue incremental sync
    async def _trigger_gdrive_sync():
        async with AsyncSessionLocal() as session:
            stmt = select(User).limit(1)
            res = await session.execute(stmt)
            user = res.scalar_one_or_none()
            if user:
                await sync_service.run_sync(user_id=user.id)

    background_tasks.add_task(_trigger_gdrive_sync)

    return Response(
        content='{"status":"accepted","resource_state":"' + resource_state + '"}',
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.get("/subscriptions")
async def list_active_subscriptions():
    """
    Lists metadata about registered Microsoft Graph and Google Drive webhook subscriptions.
    """
    return {
        "webhook_url": f"{settings.BACKEND_URL}/api/webhooks/onedrive",
        "mock_mode": settings.DEV_MOCK_ONEDRIVE,
        "client_state_configured": bool(settings.WEBHOOK_CLIENT_STATE),
        "supported_change_types": ["created", "updated", "deleted"],
        "resource": "/me/drive/root",
        "google_webhook_url": f"{settings.BACKEND_URL}/api/webhooks/gdrive",
        "google_mock_mode": settings.DEV_MOCK_GDRIVE,
        "google_token_configured": bool(settings.GOOGLE_WEBHOOK_SECRET),
        "google_resource": "https://www.googleapis.com/drive/v3/changes",
    }

