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

DRIVE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,webViewLink,parents,trashed,shortcutDetails"

# Google Workspace files must be exported; everything else downloads as-is.
EXPORT_FORMATS: Dict[str, str] = {
    "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
PDF = "application/pdf"


def _q(value: str) -> str:
    """Escapes a value for a Drive query string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleDriveConnector:
    drive_type = "google_drive"
    service = "Google Drive"

    def __init__(self) -> None:
        self._root_ids: Dict[str, str] = {}

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
        )

    async def get_root(self, token: str) -> DriveItem:
        query = (
            f"name = '{_q(settings.GOOGLE_DRIVE_ROOT_FOLDER)}' and 'root' in parents "
            f"and mimeType = '{FOLDER_MIME}' and trashed = false"
        )
        with trace_drive_call("google_drive", "get_root"):
            res = await api_request("GET", f"{DRIVE}/files", token, self.service,
                                    params={"q": query, "fields": f"files({_FIELDS})", "pageSize": "1"})
        files = res.json().get("files", [])
        if not files:
            raise RootFolderMissingError(
                f"Create a folder named '{settings.GOOGLE_DRIVE_ROOT_FOLDER}' in My Drive "
                "and put the documents you want to index inside it."
            )
        root = self._item(files[0], "/")
        root.name = ""
        self._root_ids[token[-16:]] = root.id
        return root

    async def list_children(self, token: str, folder_id: str, folder_path: str) -> List[DriveItem]:
        params: Optional[dict] = {
            "q": f"'{_q(folder_id)}' in parents and trashed = false",
            "fields": f"nextPageToken, files({_FIELDS})",
            "pageSize": "1000",
            "orderBy": "folder,name",
        }
        items: List[DriveItem] = []
        with trace_drive_call("google_drive", "list_children"):
            while params:
                data = (await api_request("GET", f"{DRIVE}/files", token, self.service, params=params)).json()
                for raw in data.get("files", []):
                    if raw.get("mimeType") == SHORTCUT_MIME:
                        target = raw.get("shortcutDetails") or {}
                        if target.get("targetMimeType") == FOLDER_MIME:
                            continue  # shortcut folders could escape the root; skip them
                        raw = {**raw, "id": target.get("targetId", raw["id"]), "mimeType": target.get("targetMimeType", "")}
                    items.append(self._item(raw, folder_path))
                token_ = data.get("nextPageToken")
                params = {**params, "pageToken": token_} if token_ else None
        return items

    async def _root_id(self, token: str) -> str:
        return self._root_ids.get(token[-16:]) or (await self.get_root(token)).id

    async def get_file(self, token: str, file_id: str) -> DriveItem:
        with trace_drive_call("google_drive", "get_file"):
            raw = (await api_request("GET", f"{DRIVE}/files/{quote(file_id, safe='')}", token, self.service,
                                     params={"fields": _FIELDS})).json()
            if raw.get("mimeType") == SHORTCUT_MIME:
                target_id = (raw.get("shortcutDetails") or {}).get("targetId")
                if not target_id:
                    raise NotFoundError("Broken Google Drive shortcut")
                parents = raw.get("parents")
                raw = (await api_request("GET", f"{DRIVE}/files/{quote(target_id, safe='')}", token, self.service,
                                         params={"fields": _FIELDS})).json()
                raw["parents"] = parents  # place the target where the shortcut lives
            if raw.get("trashed"):
                raise NotFoundError("Google Drive file is in the trash")
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
            raw = (await api_request("GET", f"{DRIVE}/files/{quote(current, safe='')}", token, self.service,
                                     params={"fields": "id,name,parents"})).json()
            names.append(raw.get("name", ""))
            current = (raw.get("parents") or [None])[0]
        raise NotFoundError(f"The file is not inside the '{settings.GOOGLE_DRIVE_ROOT_FOLDER}' folder")

    async def download(self, token: str, item: DriveItem) -> bytes:
        if item.is_folder:
            raise ConnectorError("Folders cannot be downloaded")
        file_url = f"{DRIVE}/files/{quote(item.id, safe='')}"
        limit = settings.max_file_size_bytes
        with trace_drive_call("google_drive", "download"):
            if not item.mime_type.startswith("application/vnd.google-apps."):
                return await api_request("GET", file_url, token, self.service, params={"alt": "media"}, max_bytes=limit)
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
