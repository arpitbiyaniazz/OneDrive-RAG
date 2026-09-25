import fitz  # PyMuPDF
from typing import List
from app.processors.base import BaseDocumentParser, DocumentElement, ParsedDocument


class PDFParser(BaseDocumentParser):
    """Extracts text page-by-page from PDF files using PyMuPDF."""

    def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        elements: List[DocumentElement] = []
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        total_pages = len(doc)

        for page_idx in range(total_pages):
            page = doc[page_idx]
            page_text = page.get_text("text").strip()
            if page_text:
                elements.append(
                    DocumentElement(
                        text=page_text,
                        page_number=page_idx + 1,
                        section=f"Page {page_idx + 1}",
                        metadata={
                            "page_number": page_idx + 1,
                            "total_pages": total_pages,
                        }
                    )
                )

        doc.close()
        return ParsedDocument(
            elements=elements,
            file_type="pdf",
            total_pages_or_slides=total_pages,
            metadata={"filename": filename, "format": "pdf"}
        )
