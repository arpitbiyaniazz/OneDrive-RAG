"""OCR (OCR_PROVIDER=mistral) for scanned PDFs, scanned pages and images, with the OCR API mocked."""
import base64
import json
from datetime import UTC, datetime

import httpx
import pymupdf
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.services import ocr_service
from app.services.connectors import DriveItem, base
from app.services.connectors.sandbox import sandbox_onedrive
from app.services.indexing import index_file
from app.services.jobs import drain_jobs
from tests.conftest import make_user
from tests.fixtures.corpus import _pdf_scanned


def _install(monkeypatch, handler):
    monkeypatch.setattr(base, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ocr_service.asyncio, "sleep", no_sleep)


@pytest.fixture
def ocr_api(monkeypatch):
    """Turns OCR on and answers every page with recognizable text; records pages per request."""
    monkeypatch.setattr(settings, "OCR_PROVIDER", "mistral")
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "test-key")
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ocr") and request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == settings.OCR_MODEL
        pdf = base64.b64decode(body["document"]["document_url"].split(",", 1)[1])
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            sent.append(doc.page_count)
            pages = [{"index": i, "markdown": f"Scanned invoice ZX-{i + 1} total 99 EUR ![img-0.jpeg](img-0.jpeg)"}
                     for i in range(doc.page_count)]
        return httpx.Response(200, json={"pages": pages, "model": "mistral-ocr"})

    _install(monkeypatch, handler)
    return sent


def _item(name, mime, item_id="ocr-1"):
    return DriveItem(item_id, name, False, "onedrive", mime, modified_date=datetime(2026, 9, 1, tzinfo=UTC))


async def _chunks(user_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(DocumentChunk).where(DocumentChunk.user_id == user_id).order_by(DocumentChunk.chunk_index)
        )).scalars().all()


def _mixed_pdf() -> bytes:
    scanned = pymupdf.open(stream=_pdf_scanned(), filetype="pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 60, 545, 800), "Contract terms. " * 40, fontsize=11)
    doc.insert_pdf(scanned)
    return doc.tobytes()


def _png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=120)
    page.insert_text((20, 60), "Receipt 4471", fontsize=14)
    return page.get_pixmap(dpi=72).tobytes("png")


async def test_scanned_pdf_is_indexed_with_ocr(ocr_api):
    user, _ = await make_user("ocr@example.com")
    result = await index_file(user.id, _item("Scan.pdf", "application/pdf"), _pdf_scanned())
    assert result.status == "INDEXED", result
    chunks = await _chunks(user.id)
    assert "Scanned invoice ZX-1 total 99 EUR" in chunks[0].content and "img-0" not in chunks[0].content
    assert chunks[0].page_number == 1


async def test_only_the_scanned_pages_of_a_mixed_pdf_are_sent(ocr_api):
    user, _ = await make_user("mixed@example.com")
    assert (await index_file(user.id, _item("Contract.pdf", "application/pdf"), _mixed_pdf())).status == "INDEXED"
    assert ocr_api == [1]  # one request with only page 2
    chunks = await _chunks(user.id)
    assert any("Contract terms." in c.content for c in chunks)
    scanned = [c for c in chunks if "Scanned invoice ZX-1" in c.content]
    assert scanned and all(c.page_number <= 2 <= (c.page_end or c.page_number) for c in scanned)
    async with AsyncSessionLocal() as s:
        doc = (await s.execute(select(Document))).scalar_one()
    assert "Pages 2 were read with OCR" in doc.error_message


async def test_images_need_ocr(monkeypatch):
    user, _ = await make_user("img@example.com")
    result = await index_file(user.id, _item("receipt.png", "image/png"), _png())
    assert result.status == "UNSUPPORTED" and "OCR" in result.reason
    assert not _item("receipt.png", "image/png").supported


async def test_images_are_indexed_with_ocr(ocr_api):
    user, _ = await make_user("img2@example.com")
    item = _item("receipt.png", "image/png")
    assert item.supported
    assert (await index_file(user.id, item, _png())).status == "INDEXED"
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(Document.file_type))).scalar_one() == "png"


async def test_ocr_outages_are_retried_on_the_next_sync(monkeypatch):
    monkeypatch.setattr(settings, "OCR_PROVIDER", "mistral")
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "test-key")
    _install(monkeypatch, lambda request: httpx.Response(503, json={"detail": "busy"}))
    user, _ = await make_user("outage@example.com")
    result = await index_file(user.id, _item("Scan.pdf", "application/pdf"), _pdf_scanned())
    assert result.status == "FAILED"  # not UNSUPPORTED: a later sync tries again


async def test_turning_ocr_on_reprocesses_files_that_needed_it(client, auth_headers, monkeypatch):
    sandbox_onedrive.set_content("file_hr_leave", _pdf_scanned())
    await client.post("/api/ingestion/start", headers=auth_headers,
                      json={"items": [{"id": "file_hr_leave", "drive_type": "onedrive"}]})
    await drain_jobs()
    docs = (await client.get("/api/documents", headers=auth_headers)).json()["documents"]
    assert docs[0]["status"] == "UNSUPPORTED" and "OCR is turned off" in docs[0]["error_message"]

    await client.post("/api/sync/start", headers=auth_headers)
    await drain_jobs()
    docs = (await client.get("/api/documents", headers=auth_headers)).json()["documents"]
    assert docs[0]["status"] == "UNSUPPORTED"  # unchanged file, same settings: not downloaded again

    monkeypatch.setattr(settings, "OCR_PROVIDER", "mistral")
    monkeypatch.setattr(settings, "MISTRAL_API_KEY", "test-key")
    _install(monkeypatch, lambda request: httpx.Response(200, json={"pages": [{"index": 0, "markdown": "Leave: 18 days"}]}))
    await client.post("/api/sync/start", headers=auth_headers)
    await drain_jobs()
    docs = (await client.get("/api/documents", headers=auth_headers)).json()["documents"]
    assert docs[0]["status"] == "INDEXED"
