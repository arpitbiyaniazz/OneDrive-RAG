import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_google_auth_login():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/auth/google/login")
        assert res.status_code == 200
        data = res.json()
        assert "auth_url" in data
        assert "code=mock_google_code_123" in data["auth_url"] or "accounts.google.com" in data["auth_url"]


@pytest.mark.asyncio
async def test_google_auth_callback_and_me():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/auth/google/callback?code=mock_google_code_123")
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        assert "user" in data
        assert data.get("connected_to_gdrive") is True

        token = data["token"]
        me_res = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data.get("gdrive_connected") is True
        assert me_data.get("email") == "demo@contoso.com"


@pytest.mark.asyncio
async def test_google_drive_tree():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/gdrive/tree")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert len(data["items"]) > 0

        # Check for Google Drive folders
        folder_names = [it["name"] for it in data["items"]]
        assert "Corporate Shared Drive" in folder_names or "Cloud & Engineering Specs" in folder_names


@pytest.mark.asyncio
async def test_google_drive_folder_items():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/gdrive/folders/gdrive_folder_corporate/items")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        file_names = [it["name"] for it in data["items"]]
        assert any(".gdoc" in f or ".gsheet" in f or ".gslides" in f for f in file_names)


@pytest.mark.asyncio
async def test_google_drive_file_metadata():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/gdrive/files/file_gdrive_pdf/metadata")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "file_gdrive_pdf"
        assert data["name"] == "Kubernetes_Hybrid_Cloud_Architecture.pdf"
        assert data["drive_type"] == "google_drive"


@pytest.mark.asyncio
async def test_google_drive_delta():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/gdrive/delta")
        assert res.status_code == 200
        data = res.json()
        assert "changes" in data
        assert len(data["changes"]) > 0


@pytest.mark.asyncio
async def test_google_drive_ingestion_end_to_end():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Start ingestion of Google Drive files
        payload = {
            "item_ids": ["file_gdrive_pdf", "file_gdrive_doc", "file_gdrive_sheet"],
            "folder_path": "/GoogleDrive"
        }
        res = await ac.post("/api/ingestion/start", json=payload)
        assert res.status_code == 200
        job_data = res.json()
        assert "job_id" in job_data

        # Wait briefly for background ingestion
        import asyncio
        for _ in range(10):
            status_res = await ac.get(f"/api/ingestion/{job_data['job_id']}")
            assert status_res.status_code == 200
            s_data = status_res.json()
            if s_data["status"] in ["COMPLETED", "FAILED"]:
                break
            await asyncio.sleep(0.5)

        # Verify ingested documents
        docs_res = await ac.get("/api/documents?drive_type=google_drive")
        assert docs_res.status_code == 200
        docs_data = docs_res.json()
        assert len(docs_data["documents"]) > 0
        assert all(d["drive_type"] == "google_drive" for d in docs_data["documents"])


@pytest.mark.asyncio
async def test_google_drive_webhook():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Sync handshake
        headers_sync = {
            "X-Goog-Channel-ID": "test_gdrive_channel_001",
            "X-Goog-Resource-State": "sync",
            "X-Goog-Resource-ID": "res_001",
        }
        res_sync = await ac.post("/api/webhooks/gdrive", headers=headers_sync)
        assert res_sync.status_code == 200

        # 2. Change notification
        headers_update = {
            "X-Goog-Channel-ID": "test_gdrive_channel_001",
            "X-Goog-Resource-State": "update",
            "X-Goog-Resource-ID": "res_001",
        }
        res_update = await ac.post("/api/webhooks/gdrive", headers=headers_update)
        assert res_update.status_code == 202
