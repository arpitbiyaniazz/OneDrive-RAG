import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.user import OAuthAccount, User
from app.services.microsoft_graph import graph_service, mock_onedrive_provider
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onedrive", tags=["OneDrive Browser & File Management"])


async def _get_valid_microsoft_token(user_id: str, db: AsyncSession) -> Optional[str]:
    """Retrieves and automatically refreshes Microsoft OAuth access token if needed."""
    stmt = select(OAuthAccount).where(
        OAuthAccount.user_id == user_id,
        OAuthAccount.provider == "microsoft",
    )
    res = await db.execute(stmt)
    acc = res.scalar_one_or_none()
    if not acc:
        return None

    access_token = decrypt_token(acc.encrypted_access_token)
    if not access_token and not acc.encrypted_refresh_token:
        return None

    if acc.encrypted_refresh_token:
        try:
            refresh_token = decrypt_token(acc.encrypted_refresh_token)
            new_tokens = await graph_service.refresh_access_token(refresh_token)
            access_token = new_tokens["access_token"]
            acc.encrypted_access_token = encrypt_token(access_token)
            if new_tokens.get("refresh_token"):
                acc.encrypted_refresh_token = encrypt_token(new_tokens["refresh_token"])
            await db.commit()
            return access_token
        except Exception as e:
            logger.error(f"Failed to refresh Microsoft token for user {user_id}: {e}")

    return access_token or None


@router.get("/tree")
async def get_onedrive_tree(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full hierarchical or root tree of folders and files from OneDrive.
    Uses real Microsoft Graph API if connected, falling back smoothly to sandbox mock files.
    """
    access_token = await _get_valid_microsoft_token(current_user.id, db)

    if access_token and not settings.DEV_MOCK_ONEDRIVE:
        try:
            items = await graph_service.list_drive_items(access_token, "root")
            tree = []
            for item in items:
                item_copy = dict(item)
                if item.get("is_folder"):
                    try:
                        children = await graph_service.list_drive_items(access_token, item["id"])
                        item_copy["children"] = children
                    except Exception:
                        item_copy["children"] = []
                tree.append(item_copy)
            return {"items": tree, "provider": "onedrive", "source": "live"}
        except Exception as e:
            logger.warning(f"Microsoft Graph API root list failed ({e}). Falling back to sandbox.")

    # Fallback to sandbox mock files (HR, Finance, Engineering)
    root_items = await mock_onedrive_provider.list_drive_items("root")
    tree = []
    for item in root_items:
        item_copy = dict(item)
        if item.get("is_folder"):
            children = await mock_onedrive_provider.list_drive_items(item["id"])
            item_copy["children"] = children
        tree.append(item_copy)
    return {"items": tree, "provider": "onedrive", "source": "sandbox"}


@router.get("/folders/{folder_id}/items")
async def list_folder_items(
    folder_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists files and subfolders within a specific OneDrive folder.
    """
    if folder_id.startswith("folder_") or settings.DEV_MOCK_ONEDRIVE:
        items = await mock_onedrive_provider.list_drive_items(folder_id)
        return {"items": items}

    access_token = await _get_valid_microsoft_token(current_user.id, db)
    if access_token:
        try:
            items = await graph_service.list_drive_items(access_token, folder_id)
            return {"items": items}
        except Exception as e:
            logger.error(f"Failed to list OneDrive folder {folder_id}: {e}")

    items = await mock_onedrive_provider.list_drive_items(folder_id)
    return {"items": items}


@router.get("/files/{item_id}/metadata")
async def get_file_metadata(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves metadata for a specific OneDrive item.
    """
    mock_item = await mock_onedrive_provider.get_item_by_id(item_id)
    if mock_item:
        return mock_item

    access_token = await _get_valid_microsoft_token(current_user.id, db)
    if access_token:
        try:
            meta = await graph_service.get_file_metadata(access_token, item_id)
            return meta
        except Exception as e:
            logger.error(f"Failed to get OneDrive file metadata: {e}")

    raise HTTPException(status_code=404, detail="OneDrive file not found.")
