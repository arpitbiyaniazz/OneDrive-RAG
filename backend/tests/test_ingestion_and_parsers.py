import asyncio
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.processors.factory import DocumentParserFactory
from app.processors.pdf import PDFParser
from app.processors.docx import DOCXParser
from app.processors.xlsx import XLSXParser
from app.processors.pptx import PPTXParser
from app.processors.txt import TXTParser
from app.services.chunker import StructureAwareChunker
from app.services.microsoft_graph import mock_onedrive_provider


@pytest.mark.asyncio
async def test_pdf_parser():
    file_bytes = await mock_onedrive_provider.download_file_bytes("file_hr_leave")
    parser = DocumentParserFactory.get_parser("Leave Policy 2026.pdf")
    assert isinstance(parser, PDFParser)

    parsed = parser.parse(file_bytes, "Leave Policy 2026.pdf")
    assert parsed.file_type == "pdf"
    assert parsed.total_pages_or_slides == 2
    assert len(parsed.elements) == 2
    assert parsed.elements[0].page_number == 1
    assert "18 annual leave days" in parsed.elements[0].text
    assert parsed.elements[1].page_number == 2


@pytest.mark.asyncio
async def test_docx_parser():
    file_bytes = await mock_onedrive_provider.download_file_bytes("file_hr_handbook")
    parser = DocumentParserFactory.get_parser("Employee Handbook.docx")
    assert isinstance(parser, DOCXParser)

    parsed = parser.parse(file_bytes, "Employee Handbook.docx")
    assert parsed.file_type == "docx"
    assert any("Leave Management" in (e.section or "") for e in parsed.elements)


@pytest.mark.asyncio
async def test_xlsx_parser():
    file_bytes = await mock_onedrive_provider.download_file_bytes("file_fin_budget")
    parser = DocumentParserFactory.get_parser("Budget 2026.xlsx")
    assert isinstance(parser, XLSXParser)

    parsed = parser.parse(file_bytes, "Budget 2026.xlsx")
    assert parsed.file_type == "xlsx"
    assert len(parsed.elements) >= 1
    assert "Budget 2026" in parsed.elements[0].text
    assert "Engineering" in parsed.elements[0].text


@pytest.mark.asyncio
async def test_pptx_parser():
    file_bytes = await mock_onedrive_provider.download_file_bytes("file_eng_arch")
    parser = DocumentParserFactory.get_parser("Microservices Architecture.pptx")
    assert isinstance(parser, PPTXParser)

    parsed = parser.parse(file_bytes, "Microservices Architecture.pptx")
    assert parsed.file_type == "pptx"
    assert parsed.total_pages_or_slides >= 2
    assert parsed.elements[0].page_number == 1


@pytest.mark.asyncio
async def test_txt_parser():
    file_bytes = await mock_onedrive_provider.download_file_bytes("file_welcome")
    parser = DocumentParserFactory.get_parser("Company Overview.txt")
    assert isinstance(parser, TXTParser)

    parsed = parser.parse(file_bytes, "Company Overview.txt")
    assert "Contoso Enterprise" in parsed.full_text


def test_structure_aware_chunker():
    chunker = StructureAwareChunker(chunk_size=100, chunk_overlap=20)
    # Long text
    long_text = "This is a paragraph about enterprise AI systems. " * 10
    from app.processors.base import DocumentElement, ParsedDocument
    parsed = ParsedDocument(
        elements=[
            DocumentElement(text=long_text, page_number=4, section="Annual Leave")
        ],
        file_type="pdf",
    )
    chunks = chunker.chunk_document(parsed, {"filename": "Policy.pdf"})
    assert len(chunks) > 1
    assert chunks[0].page_number == 4
    assert chunks[0].section == "Annual Leave"
    assert chunks[0].metadata["filename"] == "Policy.pdf"


@pytest.mark.asyncio
async def test_end_to_end_ingestion_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Trigger ingestion
        payload = {
            "item_ids": ["file_hr_leave", "file_fin_budget", "file_welcome"],
            "folder_path": "/Company",
        }
        res = await ac.post("/api/ingestion/start", json=payload)
        assert res.status_code == 200
        data = res.json()
        job_id = data["job_id"]

        # 2. Wait for background task to complete
        for _ in range(20):
            await asyncio.sleep(0.3)
            status_res = await ac.get(f"/api/ingestion/{job_id}")
            if status_res.status_code == 200:
                sdata = status_res.json()
                if sdata["status"] in ["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"]:
                    break

        assert sdata["status"] == "COMPLETED"
        assert sdata["processed_files"] == 3
        assert sdata["total_chunks"] > 0

        # 3. Check /api/documents
        doc_res = await ac.get("/api/documents")
        assert doc_res.status_code == 200
        docs = doc_res.json()["documents"]
        filenames = [d["filename"] for d in docs]
        assert "Leave Policy 2026.pdf" in filenames
        assert "Budget 2026.xlsx" in filenames

        # 4. Check /api/documents/stats
        stats_res = await ac.get("/api/documents/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert stats["total_documents"] >= 3
        assert stats["total_chunks"] > 0
