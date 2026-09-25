from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.services.google_drive import get_google_drive_service, mock_google_drive_provider
from app.core.config import settings

router = APIRouter(prefix="/gdrive", tags=["Google Drive Browser & File Management"])


@router.get("/tree")
async def get_google_drive_tree(
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full hierarchical or root tree of folders and files from Google Drive.
    """
    service = get_google_drive_service()
    if settings.DEV_MOCK_GDRIVE:
        root_items = await service.list_drive_items("root")
        tree = []
        for item in root_items:
            item_copy = dict(item)
            if item.get("is_folder"):
                children = await service.list_drive_items(item["id"])
                item_copy["children"] = children
            tree.append(item_copy)
        return {"items": tree}

    return {"items": []}


@router.get("/folders/{folder_id}/items")
async def list_google_drive_folder_items(
    folder_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Lists files and subfolders within a specific Google Drive folder.
    """
    service = get_google_drive_service()
    if settings.DEV_MOCK_GDRIVE:
        items = await service.list_drive_items(folder_id)
        return {"items": items}

    return {"items": []}


@router.get("/files/{item_id}/metadata")
async def get_google_drive_file_metadata(
    item_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves metadata for a specific Google Drive item.
    """
    if settings.DEV_MOCK_GDRIVE:
        item = await mock_google_drive_provider.get_item_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Google Drive file not found.")
        return item

    raise HTTPException(status_code=404, detail="File not found")


@router.get("/delta")
async def get_google_drive_delta(
    page_token: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Returns incremental changes from Google Drive Changes API.
    """
    service = get_google_drive_service()
    changes = await service.get_delta_changes(page_token=page_token)
    return changes
