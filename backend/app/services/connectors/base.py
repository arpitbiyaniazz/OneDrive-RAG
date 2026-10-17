import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Protocol

import httpx

from app.core.config import settings
from app.processors.detect import is_supported

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class ConnectorError(Exception):
    """A drive API call failed; the message is safe to show to the user."""


class ConnectorAuthError(ConnectorError):
    """The stored grant is missing, expired or revoked: the user must reconnect the drive."""


class NotFoundError(ConnectorError):
    pass


class RootFolderMissingError(ConnectorError):
    pass


class FileTooLargeError(ConnectorError):
    pass


@dataclass
class DriveItem:
    id: str
    name: str
    is_folder: bool
    drive_type: str
    mime_type: str = ""
    size: int = 0
    modified_date: Optional[datetime] = None
    created_date: Optional[datetime] = None
    web_url: str = ""
    folder_path: str = "/"  # folder containing the item, relative to the app root folder

    @property
    def path(self) -> str:
        return (self.folder_path.rstrip("/") + "/" + self.name) if self.folder_path != "/" else "/" + self.name

    @property
    def supported(self) -> bool:
        return self.is_folder or is_supported(self.name, self.mime_type)

    def to_api(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_folder": self.is_folder,
            "drive_type": self.drive_type,
            "mime_type": self.mime_type,
            "size": self.size,
            "modified_date": self.modified_date.isoformat() if self.modified_date else None,
            "web_url": self.web_url,
            "path": self.path,
            "supported": self.supported,
        }


class Connector(Protocol):
    drive_type: str

    async def get_root(self, token: str) -> DriveItem: ...

    async def list_children(self, token: str, folder_id: str, folder_path: str) -> List[DriveItem]: ...

    async def get_file(self, token: str, file_id: str) -> DriveItem: ...

    async def download(self, token: str, item: DriveItem) -> bytes: ...


async def walk_files(connector: Connector, token: str, folder_id: str, folder_path: str, limit: int) -> List[DriveItem]:
    """Recursively lists files under a folder (breadth-first, at most `limit` files).

    `folder_path` is the folder's own path relative to the app root ("/" for the root itself).
    """
    files: List[DriveItem] = []
    queue = [(folder_id, folder_path)]
    seen = set()
    while queue and len(files) < limit:
        folder_id, path = queue.pop(0)
        if folder_id in seen:
            continue
        seen.add(folder_id)
        for item in await connector.list_children(token, folder_id, path):
            if item.is_folder:
                queue.append((item.id, item.path))
            else:
                files.append(item)
                if len(files) >= limit:
                    break
    return files


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


_client: Optional[httpx.AsyncClient] = None


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    return _client


async def close_http_client() -> None:
    if _client is not None and not _client.is_closed:
        await _client.aclose()


def _retry_after(res: httpx.Response, default: float) -> float:
    value = res.headers.get("Retry-After")
    if not value:
        return default
    try:
        return min(float(value), 60.0)
    except ValueError:
        try:
            return min(max((parsedate_to_datetime(value) - datetime.now(parsedate_to_datetime(value).tzinfo)).total_seconds(), 1.0), 60.0)
        except Exception:
            return default


async def api_request(
    method: str,
    url: str,
    token: str,
    service: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    max_bytes: Optional[int] = None,
) -> httpx.Response | bytes:
    """Calls a drive API with retries on throttling/5xx. With max_bytes, streams and returns the body."""
    headers = {"Authorization": f"Bearer {token}"}
    delay = 1.0
    for attempt in range(5):
        try:
            if max_bytes is None:
                res = await http_client().request(method, url, headers=headers, params=params)
                body = None
            else:
                async with http_client().stream(method, url, headers=headers, params=params) as res:
                    if res.status_code == 200:
                        declared = int(res.headers.get("Content-Length") or 0)
                        if declared > max_bytes:
                            raise FileTooLargeError(f"File exceeds the {settings.MAX_FILE_SIZE_MB} MB limit")
                        chunks, total = [], 0
                        async for part in res.aiter_bytes():
                            total += len(part)
                            if total > max_bytes:
                                raise FileTooLargeError(f"File exceeds the {settings.MAX_FILE_SIZE_MB} MB limit")
                            chunks.append(part)
                        return b"".join(chunks)
                    await res.aread()
                    body = res
        except httpx.TransportError as exc:
            if attempt == 4:
                raise ConnectorError(f"{service} is unreachable: {type(exc).__name__}") from exc
            await asyncio.sleep(delay)
            delay *= 2
            continue

        res = body or res
        if res.status_code < 300:
            return res
        if res.status_code in RETRY_STATUSES and attempt < 4:
            wait = _retry_after(res, delay)
            logger.warning("%s returned %s; retrying in %.1fs", service, res.status_code, wait)
            await asyncio.sleep(wait)
            delay *= 2
            continue
        if res.status_code == 401:
            raise ConnectorAuthError(f"{service} access expired or was revoked. Reconnect the drive.")
        if res.status_code == 404:
            raise NotFoundError(f"{service} item not found")
        logger.warning("%s error %s: %s", service, res.status_code, res.text[:500])
        raise ConnectorError(f"{service} request failed ({res.status_code})")
    raise ConnectorError(f"{service} request failed after retries")
