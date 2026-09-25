import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_microsoft_graph_validation_challenge():
    """
    Microsoft Graph subscription validation requires echoing back validationToken
    with plain text Content-Type and HTTP 200 within 10 seconds.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        token = "test-token-validation-xyz-12345"
        res = await ac.post(f"/api/webhooks/onedrive?validationToken={token}")
        assert res.status_code == 200
        assert res.text == token
        assert "text/plain" in res.headers["content-type"]


@pytest.mark.asyncio
async def test_microsoft_graph_change_notifications():
    """
    Simulates incoming Microsoft Graph change notifications (created, updated, deleted)
    with clientState verification.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        payload = {
            "value": [
                {
                    "subscriptionId": "sub_123",
                    "clientState": settings.WEBHOOK_CLIENT_STATE,
                    "changeType": "updated",
                    "resource": "me/drive/items/file_welcome",
                    "tenantId": "common",
                    "resourceData": {
                        "@odata.type": "#Microsoft.Graph.DriveItem",
                        "id": "file_welcome",
                    },
                }
            ]
        }
        res = await ac.post("/api/webhooks/onedrive", json=payload)
        assert res.status_code == 202
        data = res.json()
        assert data["status"] == "accepted"
        assert data["processed_notifications"] == 1


@pytest.mark.asyncio
async def test_webhook_subscriptions_info():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/webhooks/subscriptions")
        assert res.status_code == 200
        data = res.json()
        assert "webhook_url" in data
        assert data["client_state_configured"] is True
        assert "created" in data["supported_change_types"]
