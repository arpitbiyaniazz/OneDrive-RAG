from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.user import User
from app.services.connectors import (
    ConnectorAuthError,
    ConnectorError,
    NotFoundError,
    RootFolderMissingError,
    get_connector,
    is_sandbox,
)
from app.services.oauth_tokens import get_drive_token

router = APIRouter(tags=["Drive browser"])
_DRIVES = {"onedrive": "onedrive", "gdrive": "google_drive"}


def _drive(alias: str) -> str:
    if alias not in _DRIVES:
        raise HTTPException(status_code=404, detail="Unknown drive")
    return _DRIVES[alias]


def _error(exc: ConnectorError) -> HTTPException:
    # 403, not 401: the app session is fine, only the drive grant is missing or expired.
    if isinstance(exc, ConnectorAuthError):
        return HTTPException(status_code=403, detail={"code": "drive_not_connected", "message": str(exc)})
    if isinstance(exc, RootFolderMissingError):
        return HTTPException(status_code=404, detail={"code": "root_folder_missing", "message": str(exc)})
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)})
    return HTTPException(status_code=502, detail={"code": "drive_error", "message": str(exc)})


@router.get("/{alias}/tree")
async def root_items(alias: str = Path(..., pattern="^(onedrive|gdrive)$"),
                     db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Top level of the app's root folder; subfolders are loaded on demand via /folders/{id}/items."""
    drive_type = _drive(alias)
    connector = get_connector(drive_type)
    try:
        token = await get_drive_token(db, user.id, drive_type)
        root = await connector.get_root(token)
        items = await connector.list_children(token, root.id, "/")
    except ConnectorError as exc:
        raise _error(exc)
    return {"items": [i.to_api() for i in items], "root_id": root.id, "drive_type": drive_type,
            "source": "sandbox" if is_sandbox(drive_type) else "live"}


@router.get("/{alias}/folders/{folder_id}/items")
async def folder_items(folder_id: str = Path(..., max_length=255), alias: str = Path(..., pattern="^(onedrive|gdrive)$"),
                       db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    drive_type = _drive(alias)
    connector = get_connector(drive_type)
    try:
        token = await get_drive_token(db, user.id, drive_type)
        folder = await connector.get_file(token, folder_id)  # also enforces the root-folder scope
        if not folder.is_folder:
            raise NotFoundError("Not a folder")
        items = await connector.list_children(token, folder.id, folder.path)
    except ConnectorError as exc:
        raise _error(exc)
    return {"items": [i.to_api() for i in items]}
