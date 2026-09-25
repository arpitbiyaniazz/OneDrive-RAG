import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.sync_service import sync_service


@pytest.mark.asyncio
async def test_incremental_sync_pipeline():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Trigger initial sync synchronously
        res1 = await ac.post("/api/sync/start?background=false")
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "completed"
        assert "summary" in data1
        summary1 = data1["summary"]
        assert summary1["total_scanned"] > 0
        assert (
            summary1["new_files"]
            + summary1["unchanged_files"]
            + summary1["updated_files"]
        ) == summary1["total_scanned"]

        # 2. Trigger second sync - all files should now be detected as UNCHANGED
        res2 = await ac.post("/api/sync/start?background=false")
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "completed"
        summary2 = data2["summary"]
        assert summary2["new_files"] == 0
        assert summary2["unchanged_files"] == summary2["total_scanned"]

        # 3. Modify a file in OneDrive and verify sync detects it as UPDATED
        from app.services.microsoft_graph import mock_onedrive_provider
        mock_onedrive_provider.set_mock_file_content(
            "file_welcome",
            b"Updated Contoso Company Overview with latest 2026 strategic objectives."
        )
        res3 = await ac.post("/api/sync/start?background=false")
        assert res3.status_code == 200
        summary3 = res3.json()["summary"]
        assert summary3["updated_files"] == 1
        assert summary3["unchanged_files"] == summary3["total_scanned"] - 1
        assert summary3["new_files"] == 0

        # 4. Check status endpoint
        status_res = await ac.get("/api/sync/status")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["is_syncing"] is False
        assert status_data["last_result"] is not None
        assert status_data["last_result"]["total_scanned"] == summary3["total_scanned"]


@pytest.mark.asyncio
async def test_sync_background_trigger():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/api/sync/start?background=true")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ["started", "already_running"]


@pytest.mark.asyncio
async def test_delta_sync_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/sync/delta")
        assert res.status_code == 200
        data = res.json()
        assert "@odata.deltaLink" in data
        assert "value" in data
        assert len(data["value"]) > 0
