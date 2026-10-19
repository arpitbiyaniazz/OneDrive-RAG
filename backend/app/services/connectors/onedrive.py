from typing import List, Optional
from urllib.parse import quote, unquote

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

GRAPH = "https://graph.microsoft.com/v1.0"
_SELECT = "id,name,size,file,folder,webUrl,parentReference,lastModifiedDateTime,createdDateTime,deleted"


def _root_prefix() -> str:
    return "/" + settings.ONEDRIVE_ROOT_FOLDER.strip("/")


def _drive_relative(parent_path: str) -> Optional[str]:
    """'/drive/root:/A/B' (personal) or '/drives/{id}/root:/A/B' (business) -> '/A/B'.

    Graph may percent-encode the path; None when it is not under the drive root.
    """
    marker = parent_path.find("root:")
    if marker == -1:
        return None
    rest = unquote(parent_path[marker + len("root:"):])
    return rest if rest.startswith("/") else "/" + rest


class OneDriveConnector:
    drive_type = "onedrive"
    service = "OneDrive"

    def _item(self, raw: dict, folder_path: str) -> DriveItem:
        return DriveItem(
            id=raw["id"],
            name=raw.get("name", ""),
            is_folder="folder" in raw,
            drive_type=self.drive_type,
            mime_type=(raw.get("file") or {}).get("mimeType", "folder" if "folder" in raw else ""),
            size=int(raw.get("size") or 0),
            modified_date=parse_datetime(raw.get("lastModifiedDateTime")),
            created_date=parse_datetime(raw.get("createdDateTime")),
            web_url=raw.get("webUrl", ""),
            folder_path=folder_path,
        )

    async def get_root(self, token: str) -> DriveItem:
        path = quote(settings.ONEDRIVE_ROOT_FOLDER.strip("/"))
        with trace_drive_call("onedrive", "get_root"):
            try:
                res = await api_request("GET", f"{GRAPH}/me/drive/root:/{path}", token, self.service, params={"$select": _SELECT})
            except NotFoundError as exc:
                raise RootFolderMissingError(
                    f"Create a folder named '{settings.ONEDRIVE_ROOT_FOLDER}' at the top of your OneDrive "
                    "and put the documents you want to index inside it."
                ) from exc
        raw = res.json()
        if "folder" not in raw:
            raise RootFolderMissingError(f"'{settings.ONEDRIVE_ROOT_FOLDER}' in your OneDrive is not a folder")
        root = self._item(raw, "/")
        root.name = ""
        return root

    async def list_children(self, token: str, folder_id: str, folder_path: str) -> List[DriveItem]:
        url = f"{GRAPH}/me/drive/items/{quote(folder_id, safe='')}/children"
        params = {"$select": _SELECT, "$top": "200"}
        items: List[DriveItem] = []
        with trace_drive_call("onedrive", "list_children"):
            while url:
                data = (await api_request("GET", url, token, self.service, params=params)).json()
                items.extend(self._item(raw, folder_path) for raw in data.get("value", []) if "deleted" not in raw)
                url, params = data.get("@odata.nextLink"), None  # nextLink already carries the query
        return items

    async def get_file(self, token: str, file_id: str) -> DriveItem:
        with trace_drive_call("onedrive", "get_file"):
            res = await api_request("GET", f"{GRAPH}/me/drive/items/{quote(file_id, safe='')}", token, self.service,
                                    params={"$select": _SELECT})
        raw = res.json()
        if "deleted" in raw:
            raise NotFoundError("OneDrive item was deleted")
        parent = _drive_relative((raw.get("parentReference") or {}).get("path", ""))
        prefix = _root_prefix().lower()
        if parent is None:
            raise NotFoundError(f"The file is not inside the '{settings.ONEDRIVE_ROOT_FOLDER}' folder")
        if "folder" in raw and (parent.rstrip("/") + "/" + raw.get("name", "")).lower() == prefix:
            root = self._item(raw, "/")
            root.name = ""
            return root
        if parent.lower() != prefix and not parent.lower().startswith(prefix + "/"):
            raise NotFoundError(f"The file is not inside the '{settings.ONEDRIVE_ROOT_FOLDER}' folder")
        return self._item(raw, parent[len(prefix):] or "/")

    async def download(self, token: str, item: DriveItem) -> bytes:
        if item.is_folder:
            raise ConnectorError("Folders cannot be downloaded")
        with trace_drive_call("onedrive", "download"):
            return await api_request("GET", f"{GRAPH}/me/drive/items/{quote(item.id, safe='')}/content", token,
                                     self.service, max_bytes=settings.max_file_size_bytes)
