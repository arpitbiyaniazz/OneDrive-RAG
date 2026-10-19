"""The real OneDrive and Google Drive connectors against recorded-style API responses (no network)."""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.services.connectors import base
from app.services.connectors.base import ConnectorAuthError, NotFoundError, api_request, walk_files
from app.services.connectors.google_drive import GoogleDriveConnector
from app.services.connectors.onedrive import OneDriveConnector


@pytest.fixture
def drive_api(monkeypatch):
    """Routes connector HTTP calls to a handler: routes[(method, path)] -> callable(request) -> Response."""
    routes = {}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        key = (request.method, request.url.path)
        if key not in routes:
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        return routes[key](request)

    monkeypatch.setattr(base, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(base.asyncio, "sleep", no_sleep)
    return routes, calls


def _json(payload, status=200, headers=None):
    return lambda request: httpx.Response(status, json=payload, headers=headers)


def _graph_item(item_id, name, parent_path, folder=False, **extra):
    raw = {"id": item_id, "name": name, "webUrl": f"https://onedrive.example/{item_id}",
           "lastModifiedDateTime": "2026-09-01T10:00:00Z", "parentReference": {"path": parent_path}, **extra}
    raw["folder" if folder else "file"] = {"childCount": 1} if folder else {"mimeType": "application/pdf"}
    return raw


async def test_onedrive_business_paths_are_scoped_and_decoded(drive_api):
    routes, _ = drive_api
    business = "/drives/b!abc/root:"
    routes[("GET", "/v1.0/me/drive/items/f1")] = _json(_graph_item("f1", "Plan.pdf", f"{business}/OneDrive-RAG/Sub%20Folder"))
    routes[("GET", "/v1.0/me/drive/items/root1")] = _json(_graph_item("root1", "OneDrive-RAG", business, folder=True))
    routes[("GET", "/v1.0/me/drive/items/out")] = _json(_graph_item("out", "Secret.pdf", f"{business}/Private"))
    connector = OneDriveConnector()

    item = await connector.get_file("t", "f1")
    assert item.folder_path == "/Sub Folder" and item.path == "/Sub Folder/Plan.pdf"
    root = await connector.get_file("t", "root1")
    assert root.is_folder and root.name == "" and root.path == "/"
    with pytest.raises(NotFoundError):
        await connector.get_file("t", "out")


async def test_onedrive_listing_follows_pagination(drive_api):
    routes, _ = drive_api
    page2 = "https://graph.microsoft.com/v1.0/me/drive/items/root1/children/page2"
    routes[("GET", "/v1.0/me/drive/items/root1/children")] = _json(
        {"value": [_graph_item("a", "A.pdf", "/drive/root:/OneDrive-RAG")], "@odata.nextLink": page2})
    routes[("GET", "/v1.0/me/drive/items/root1/children/page2")] = _json(
        {"value": [_graph_item("b", "B.pdf", "/drive/root:/OneDrive-RAG")]})
    files = await walk_files(OneDriveConnector(), "t", "root1", "/", limit=100)
    assert [f.name for f in files] == ["A.pdf", "B.pdf"]


async def test_throttling_is_retried_and_revoked_tokens_are_reported(drive_api):
    routes, calls = drive_api
    responses = iter([httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"ok": True})])
    routes[("GET", "/throttled")] = lambda request: next(responses)
    res = await api_request("GET", "https://api.example/throttled", "t", "Test")
    assert res.json() == {"ok": True} and len(calls) == 2

    google_quota = iter([
        httpx.Response(403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}),
        httpx.Response(200, json={"ok": True}),
    ])
    routes[("GET", "/quota")] = lambda request: next(google_quota)
    assert (await api_request("GET", "https://api.example/quota", "t", "Test")).json() == {"ok": True}

    routes[("GET", "/revoked")] = _json({"error": "invalid_token"}, status=401)
    with pytest.raises(ConnectorAuthError):
        await api_request("GET", "https://api.example/revoked", "t", "Test")


def _gfile(file_id, name, mime="application/pdf", parents=("root-folder",), **extra):
    return {"id": file_id, "name": name, "mimeType": mime, "parents": list(parents),
            "modifiedTime": "2026-09-01T10:00:00Z", "webViewLink": f"https://drive.example/{file_id}", **extra}


async def test_google_shortcuts_keep_their_place_and_download_the_target(drive_api):
    routes, calls = drive_api
    routes[("GET", "/drive/v3/files")] = lambda request: httpx.Response(200, json=(
        {"files": [_gfile("root-folder", "OneDrive-RAG", "application/vnd.google-apps.folder", parents=["root"])]}
        if "'root' in parents" in request.url.params["q"] else
        {"files": [_gfile("sc1", "Contract (shortcut)", "application/vnd.google-apps.shortcut",
                          shortcutDetails={"targetId": "target1", "targetMimeType": "application/pdf"}),
                   _gfile("sc2", "Elsewhere", "application/vnd.google-apps.shortcut",
                          shortcutDetails={"targetId": "folder9", "targetMimeType": "application/vnd.google-apps.folder"})]}
    ))
    routes[("GET", "/drive/v3/files/target1")] = lambda request: httpx.Response(200, json=(
        b"%PDF-1.7 target bytes" if request.url.params.get("alt") == "media" else
        _gfile("target1", "Contract.pdf", parents=["other-folder"], modifiedTime="2026-09-20T08:00:00Z")
    )) if request.url.params.get("alt") != "media" else httpx.Response(200, content=b"%PDF-1.7 target bytes")
    routes[("GET", "/drive/v3/files/sc1")] = _json(
        _gfile("sc1", "Contract (shortcut)", "application/vnd.google-apps.shortcut",
               shortcutDetails={"targetId": "target1", "targetMimeType": "application/pdf"}))
    connector = GoogleDriveConnector()
    root = await connector.get_root("token-1234567890abcdef")

    [item] = await connector.list_children("token-1234567890abcdef", root.id, "/")
    assert (item.id, item.content_id, item.name, item.mime_type) == ("sc1", "target1", "Contract (shortcut)", "application/pdf")
    assert item.modified_date.day == 20  # the target's date, so sync notices edits to the target

    again = await connector.get_file("token-1234567890abcdef", "sc1")
    assert again.id == "sc1" and again.folder_path == "/"  # placed where the shortcut lives, not outside the root
    assert await connector.download("token-1234567890abcdef", item) == b"%PDF-1.7 target bytes"
    assert all(parse_qs(urlparse(str(c.url)).query).get("supportsAllDrives") == ["true"]
               for c in calls if c.url.path.startswith("/drive/v3/files/"))


async def test_google_files_outside_the_root_folder_are_rejected(drive_api):
    routes, _ = drive_api
    routes[("GET", "/drive/v3/files")] = _json(
        {"files": [_gfile("root-folder", "OneDrive-RAG", "application/vnd.google-apps.folder", parents=["root"])]})
    routes[("GET", "/drive/v3/files/doc1")] = _json(_gfile("doc1", "Private.pdf", parents=["mydrive-root"]))
    routes[("GET", "/drive/v3/files/mydrive-root")] = _json({"id": "mydrive-root", "name": "My Drive", "parents": []})
    with pytest.raises(NotFoundError):
        await GoogleDriveConnector().get_file("token-abcdefghijklmnop", "doc1")


async def test_oauth_network_errors_become_connector_errors(monkeypatch):
    from app.services.connectors.base import ConnectorError
    from app.services.oauth import GoogleOAuth

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(base, "_client", httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(ConnectorError):
        await GoogleOAuth().refresh("refresh-token")


def test_mock_payloads_are_json_serializable():
    # Guard for the fixtures above: Graph/Drive payloads must be plain JSON.
    assert json.loads(json.dumps(_graph_item("x", "X.pdf", "/drive/root:/OneDrive-RAG")))["name"] == "X.pdf"
