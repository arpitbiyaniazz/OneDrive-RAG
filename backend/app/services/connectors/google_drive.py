import logging
from collections import OrderedDict
from typing import Dict, List, Optional
from urllib.parse import quote

from app.core.config import settings
from app.core.telemetry.otel_setup import trace_drive_call
from app.services.connectors.base import (
    ConnectorError,
    DriveItem,
    NotFoundError,
    RootFolderMissingError,
    api_request,
    parse_datetime,
)

logger = logging.getLogger(__name__)

DRIVE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,webViewLink,parents,trashed,shortcutDetails"
# Shortcut targets and shared files can live in shared drives; harmless for My Drive files.
_ALL_DRIVES = {"supportsAllDrives": "true"}

# Google Workspace files must be exported; everything else downloads as-is.
EXPORT_FORMATS: Dict[str, str] = {
    "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
PDF = "application/pdf"
_ROOT_CACHE_SIZE = 512


def _q(value: str) -> str:
    """Escapes a value for a Drive query string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveConnector:
    drive_type = "google_drive"
    service = "Google Drive"

    def __init__(self) -> None:
        self._root_ids: OrderedDict[str, str] = OrderedDict()  # token suffix -> root folder id

    def _item(self, raw: dict, folder_path: str) -> DriveItem:
        return DriveItem(
            id=raw["id"],
            name=raw.get("name", ""),
            is_folder=raw.get("mimeType") == FOLDER_MIME,
            drive_type=self.drive_type,
            mime_type=raw.get("mimeType", ""),
            size=int(raw.get("size") or 0),
            modified_date=parse_datetime(raw.get("modifiedTime")),
            created_date=parse_datetime(raw.get("createdTime")),
            web_url=raw.get("webViewLink", ""),
            folder_path=folder_path,
            content_id=raw.get("contentId"),
        )

    async def _get(self, token: str, file_id: str, fields: str = _FIELDS) -> dict:
        return (await api_request("GET", f"{DRIVE}/files/{quote(file_id, safe='')}", token, self.service,
                                  params={"fields": fields, **_ALL_DRIVES})).json()

    async def _resolve_shortcut(self, token: str, raw: dict) -> Optional[dict]:
        """A file shortcut keeps its own id, name and place; content and dates come from the target.

        Returns None for folder shortcuts (they could reach outside the root folder) and broken ones.
        """
        target = raw.get("shortcutDetails") or {}
        if target.get("targetMimeType") == FOLDER_MIME or not target.get("targetId"):
            return None
        try:
            resolved = await self._get(token, target["targetId"])
        except NotFoundError:
            return None
        if resolved.get("trashed") or resolved.get("mimeType") == FOLDER_MIME:
            return None
        return {**resolved, "id": raw["id"], "name": raw.get("name") or resolved.get("name", ""),
                "parents": raw.get("parents"), "contentId": resolved["id"]}

    def _remember_root(self, token: str, root_id: str) -> None:
        self._root_ids[token[-16:]] = root_id
        self._root_ids.move_to_end(token[-16:])
        while len(self._root_ids) > _ROOT_CACHE_SIZE:
            self._root_ids.popitem(last=False)

    async def get_root(self, token: str) -> DriveItem:
        query = (
            f"name = '{_q(settings.GOOGLE_DRIVE_ROOT_FOLDER)}' and 'root' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        with trace_drive_call("google_drive", "get_root"):
            res = await api_request("GET", f"{DRIVE}/files", token, self.service,
                                    params={"q": query, "fields": f"files({_FIELDS})", "pageSize": "2",
                                            "orderBy": "createdTime"})
        files = res.json().get("files", [])
        if len(files) > 1:  # always the oldest, so the choice cannot flip between calls
            logger.warning("Several '%s' folders in My Drive; using the oldest", settings.GOOGLE_DRIVE_ROOT_FOLDER)
        if not files:
            raise RootFolderMissingError(
                f"Create a folder named '{settings.GOOGLE_DRIVE_ROOT_FOLDER}' in My Drive "
                "and put the documents you want to index inside it."
            )
        root = self._item(files[0], "/")
        root.name = ""
        self._remember_root(token, root.id)
        return root

    async def list_children(self, token: str, folder_id: str, folder_path: str) -> List[DriveItem]:
        params: Optional[dict] = {
            "q": f"'{_q(folder_id)}' in parents and trashed = false",
            "fields": f"nextPageToken, files({_FIELDS})",
            "pageSize": "1000",
            "orderBy": "folder,name",
            "includeItemsFromAllDrives": "true",
            **_ALL_DRIVES,
        }
        items: List[DriveItem] = []
        with trace_drive_call("google_drive", "list_children"):
            while params:
                data = (await api_request("GET", f"{DRIVE}/files", token, self.service, params=params)).json()
                for raw in data.get("files", []):
                    if raw.get("mimeType") == SHORTCUT_MIME:
                        raw = await self._resolve_shortcut(token, raw)
                        if raw is None:
                            continue
                    items.append(self._item(raw, folder_path))
                page = data.get("nextPageToken")
                params = {**params, "pageToken": page} if page else None
        return items

    async def _root_id(self, token: str) -> str:
        cached = self._root_ids.get(token[-16:])
        return cached or (await self.get_root(token)).id

    async def get_file(self, token: str, file_id: str) -> DriveItem:
        with trace_drive_call("google_drive", "get_file"):
            raw = await self._get(token, file_id)
            if raw.get("trashed"):
                raise NotFoundError("Google Drive file is in the trash")
            if raw.get("mimeType") == SHORTCUT_MIME:
                raw = await self._resolve_shortcut(token, raw)
                if raw is None:
                    raise NotFoundError("The shortcut's target is missing or is a folder")
            if raw["id"] == await self._root_id(token):
                root = self._item(raw, "/")
                root.name = ""
                return root
            folder_path = await self._folder_path(token, (raw.get("parents") or [None])[0])
        return self._item(raw, folder_path)

    async def _folder_path(self, token: str, parent_id: Optional[str]) -> str:
        """Resolves the parent chain up to the app root folder; files outside it are rejected."""
        root_id = await self._root_id(token)
        names: List[str] = []
        current = parent_id
        for _ in range(25):
            if current is None:
                break
            if current == root_id:
                return "/" + "/".join(reversed(names)) if names else "/"
            raw = await self._get(token, current, fields="id,name,parents")
            names.append(raw.get("name", ""))
            current = (raw.get("parents") or [None])[0]
        raise NotFoundError(f"The file is not inside the '{settings.GOOGLE_DRIVE_ROOT_FOLDER}' folder")

    async def download(self, token: str, item: DriveItem) -> bytes:
        if item.is_folder:
            raise ConnectorError("Folders cannot be downloaded")
        file_url = f"{DRIVE}/files/{quote(item.content_id or item.id, safe='')}"
        limit = settings.max_file_size_bytes
        with trace_drive_call("google_drive", "download"):
            if not item.mime_type.startswith("application/vnd.google-apps."):
                return await api_request("GET", file_url, token, self.service,
                                         params={"alt": "media", **_ALL_DRIVES}, max_bytes=limit)
            export = EXPORT_FORMATS.get(item.mime_type, PDF)
            try:
                return await api_request("GET", f"{file_url}/export", token, self.service,
                                         params={"mimeType": export}, max_bytes=limit)
            except ConnectorError:
                if export == PDF:
                    raise
                # Exports above Google's 10 MB limit (or unsupported conversions) fall back to PDF.
                return await api_request("GET", f"{file_url}/export", token, self.service,
                                         params={"mimeType": PDF}, max_bytes=limit)
