from app.services.diagnostics import run_all


async def test_doctor_reports_database_providers_and_configuration():
    checks = {c.name: c for c in await run_all()}
    assert checks["database"].ok and checks["migrations"].ok and checks["vector column"].ok
    assert checks["chat model (mock)"].ok and checks["embeddings (mock)"].ok
    assert not checks["Microsoft sign-in"].required and "OCR" in checks
