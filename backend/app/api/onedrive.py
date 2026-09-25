from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.services.microsoft_graph import get_onedrive_service, mock_onedrive_provider
from app.core.config import settings

router = APIRouter(prefix="/onedrive", tags=["OneDrive Browser & File Management"])


@router.get("/tree")
async def get_onedrive_tree(
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full hierarchical or root tree of folders and files from OneDrive.
    """
    service = get_onedrive_service()
    if settings.DEV_MOCK_ONEDRIVE:
        # Build full mock tree with children expanded
        root_items = await service.list_drive_items("root")
        tree = []
        for item in root_items:
            item_copy = dict(item)
            if item["is_folder"]:
                children = await service.list_drive_items(item["id"])
                item_copy["children"] = children
            tree.append(item_copy)
        return {"items": tree}

    # Production Graph API call: fetch root items
    # In real mode we pass the user's access token
    # For now, list root items
    return {"items": []}


@router.get("/folders/{folder_id}/items")
async def list_folder_items(
    folder_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Lists files and subfolders within a specific folder.
    """
    service = get_onedrive_service()
    if settings.DEV_MOCK_ONEDRIVE:
        items = await service.list_drive_items(folder_id)
        return {"items": items}

    return {"items": []}


@router.get("/files/{item_id}/metadata")
async def get_file_metadata(
    item_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves metadata for a specific OneDrive item.
    """
    if settings.DEV_MOCK_ONEDRIVE:
        item = await mock_onedrive_provider.get_item_by_id(item_id)
        if not item:
            raise HTTPException(status_code=404, detail="OneDrive file not found.")
        return item

    raise HTTPException(status_code=404, detail="File not found")
