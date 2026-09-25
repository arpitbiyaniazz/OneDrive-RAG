import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.user import OAuthAccount, User
from app.services.google_drive import google_drive_service, mock_google_drive_provider
from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gdrive", tags=["Google Drive Browser & File Management"])


async def _get_valid_google_token(user_id: str, db: AsyncSession) -> Optional[str]:
    """Retrieves and automatically refreshes Google OAuth access token if needed."""
    stmt = select(OAuthAccount).where(
        OAuthAccount.user_id == user_id,
        OAuthAccount.provider == "google",
    )
    res = await db.execute(stmt)
    acc = res.scalar_one_or_none()
    if not acc:
        return None

    access_token = decrypt_token(acc.encrypted_access_token)
    if not access_token and not acc.encrypted_refresh_token:
        return None

    # Test token or proactively refresh if needed
    try:
        # Quick check if token works
        if access_token:
            return access_token
    except Exception:
        pass

    if acc.encrypted_refresh_token:
        try:
            refresh_token = decrypt_token(acc.encrypted_refresh_token)
            new_tokens = await google_drive_service.refresh_access_token(refresh_token)
            access_token = new_tokens["access_token"]
            acc.encrypted_access_token = encrypt_token(access_token)
            if new_tokens.get("refresh_token"):
                acc.encrypted_refresh_token = encrypt_token(new_tokens["refresh_token"])
            await db.commit()
            return access_token
        except Exception as e:
            logger.error(f"Failed to refresh Google access token for user {user_id}: {e}")

    return access_token or None


@router.get("/tree")
async def get_google_drive_tree(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full hierarchical or root tree of folders and files from Google Drive.
    Uses real Google Drive API v3 if connected, falling back smoothly to sandbox mock files.
    """
    access_token = await _get_valid_google_token(current_user.id, db)

    if access_token and not settings.DEV_MOCK_GDRIVE:
        try:
            items = await google_drive_service.list_drive_items(access_token, "root")
            tree = []
            for item in items:
                item_copy = dict(item)
                if item.get("is_folder"):
                    try:
                        children = await google_drive_service.list_drive_items(access_token, item["id"])
                        item_copy["children"] = children
                    except Exception:
                        item_copy["children"] = []
                tree.append(item_copy)
            return {"items": tree, "provider": "google_drive", "source": "live"}
        except Exception as e:
            logger.warning(f"Google Drive API root list failed ({e}). Trying refresh...")
            # If 401 error, try token refresh
            stmt = select(OAuthAccount).where(
                OAuthAccount.user_id == current_user.id,
                OAuthAccount.provider == "google",
            )
            res = await db.execute(stmt)
            acc = res.scalar_one_or_none()
            if acc and acc.encrypted_refresh_token:
                try:
                    r_tok = decrypt_token(acc.encrypted_refresh_token)
                    new_t = await google_drive_service.refresh_access_token(r_tok)
                    new_access = new_t["access_token"]
                    acc.encrypted_access_token = encrypt_token(new_access)
                    await db.commit()
                    items = await google_drive_service.list_drive_items(new_access, "root")
                    tree = []
                    for item in items:
                        item_copy = dict(item)
                        if item.get("is_folder"):
                            try:
                                children = await google_drive_service.list_drive_items(new_access, item["id"])
                                item_copy["children"] = children
                            except Exception:
                                item_copy["children"] = []
                        tree.append(item_copy)
                    return {"items": tree, "provider": "google_drive", "source": "live"}
                except Exception as re:
                    logger.error(f"Token refresh retry also failed: {re}")

    # Fallback to rich mock Google Drive files for sandbox mode or unlinked accounts
    root_items = await mock_google_drive_provider.list_drive_items("root")
    tree = []
    for item in root_items:
        item_copy = dict(item)
        if item.get("is_folder"):
            children = await mock_google_drive_provider.list_drive_items(item["id"])
            item_copy["children"] = children
        tree.append(item_copy)
    return {"items": tree, "provider": "google_drive", "source": "sandbox"}


@router.get("/folders/{folder_id}/items")
async def list_google_drive_folder_items(
    folder_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists files and subfolders within a specific Google Drive folder.
    """
    # Check mock provider first if folder is mock
    if folder_id.startswith("gdrive_folder_") or settings.DEV_MOCK_GDRIVE:
        items = await mock_google_drive_provider.list_drive_items(folder_id)
        return {"items": items}

    access_token = await _get_valid_google_token(current_user.id, db)
    if access_token:
        try:
            items = await google_drive_service.list_drive_items(access_token, folder_id)
            return {"items": items}
        except Exception as e:
            logger.error(f"Failed to list Google Drive folder {folder_id}: {e}")

    items = await mock_google_drive_provider.list_drive_items(folder_id)
    return {"items": items}


@router.get("/files/{item_id}/metadata")
async def get_google_drive_file_metadata(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves metadata for a specific Google Drive item.
    """
    # Check mock provider first
    mock_item = await mock_google_drive_provider.get_item_by_id(item_id)
    if mock_item:
        return mock_item

    access_token = await _get_valid_google_token(current_user.id, db)
    if access_token:
        try:
            meta = await google_drive_service.get_file_metadata(access_token, item_id)
            return {
                "id": meta["id"],
                "name": meta["name"],
                "mime_type": meta.get("mimeType", ""),
                "size": int(meta.get("size", 0)),
                "created_date": meta.get("createdTime"),
                "modified_date": meta.get("modifiedTime"),
                "web_url": meta.get("webViewLink", ""),
                "drive_type": "google_drive",
            }
        except Exception as e:
            logger.error(f"Failed to get Google Drive metadata for {item_id}: {e}")

    raise HTTPException(status_code=404, detail="Google Drive file not found.")


@router.get("/delta")
async def get_google_drive_delta(
    page_token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns incremental changes from Google Drive Changes API.
    """
    access_token = await _get_valid_google_token(current_user.id, db)
    if access_token and not settings.DEV_MOCK_GDRIVE:
        try:
            changes = await google_drive_service.get_delta_changes(access_token, page_token=page_token)
            return changes
        except Exception as e:
            logger.error(f"Failed to fetch live delta changes: {e}")

    return await mock_google_drive_provider.get_delta_changes(page_token=page_token)

