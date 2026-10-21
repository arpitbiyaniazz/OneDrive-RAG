async def test_liveness(client):
    res = await client.get("/api/health")
    assert res.status_code == 200 and res.json()["status"] == "ok"


async def test_readiness_checks_the_migrated_database(client):
    res = await client.get("/api/health/ready")
    assert res.status_code == 200
    assert res.json()["schema_revision"] == "0004_chat_questions"


async def test_responses_carry_request_ids_and_hardening_headers(client):
    res = await client.get("/api/health", headers={"X-Request-Id": "abc123"})
    assert res.headers["X-Request-Id"] == "abc123"
    assert res.headers["X-Content-Type-Options"] == "nosniff"
