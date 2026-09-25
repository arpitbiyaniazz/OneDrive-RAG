import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_auth_login():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/auth/login")
    assert res.status_code == 200
    data = res.json()
    assert "auth_url" in data
    assert "mode" in data


@pytest.mark.asyncio
async def test_auth_callback_and_me():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Callback with mock code
        res = await ac.get("/api/auth/callback?code=mock_dev_code_123")
        assert res.status_code == 200
        data = res.json()
        assert "token" in data
        token = data["token"]

        # Call /me with Bearer token
        headers = {"Authorization": f"Bearer {token}"}
        me_res = await ac.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["email"] == "demo@contoso.com"
        assert me_data["onedrive_connected"] is True


@pytest.mark.asyncio
async def test_onedrive_tree():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/onedrive/tree")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    items = data["items"]
    # Check that root has HR, Finance, Engineering folders
    names = [i["name"] for i in items]
    assert "HR" in names
    assert "Finance" in names
    assert "Engineering" in names


@pytest.mark.asyncio
async def test_onedrive_folder_items():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/onedrive/folders/folder_hr/items")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    hr_files = [i["name"] for i in data["items"]]
    assert any("Leave Policy" in f for f in hr_files)


@pytest.mark.asyncio
async def test_onedrive_file_metadata():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/onedrive/files/file_hr_leave/metadata")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Leave Policy 2026.pdf"
    assert data["mime_type"] == "application/pdf"
    assert "web_url" in data
