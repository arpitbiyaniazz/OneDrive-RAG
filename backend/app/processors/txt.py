from typing import List
from app.processors.base import BaseDocumentParser, DocumentElement, ParsedDocument


class TXTParser(BaseDocumentParser):
    """Parses plain text (.txt, .md, .csv) with multiple encoding fallbacks."""

    def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        # Try decodings in order: UTF-8, Latin-1, CP1252
        text = ""
        for encoding in ["utf-8", "latin-1", "cp1252"]:
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        # Split into logical paragraphs
        raw_paragraphs = text.split("\n\n")
        elements: List[DocumentElement] = []

        current_block = []
        current_char_count = 0

        for p in raw_paragraphs:
            cleaned = p.strip()
            if not cleaned:
                continue
            current_block.append(cleaned)
            current_char_count += len(cleaned)

            if current_char_count >= 800:
                elements.append(
                    DocumentElement(
                        text="\n\n".join(current_block),
                        section=f"Section {len(elements) + 1}",
                        metadata={"section_index": len(elements) + 1}
                    )
                )
                current_block = []
                current_char_count = 0

        if current_block:
            elements.append(
                DocumentElement(
                    text="\n\n".join(current_block),
                    section=f"Section {len(elements) + 1}",
                    metadata={"section_index": len(elements) + 1}
                )
            )

        return ParsedDocument(
            elements=elements,
            file_type="txt",
            total_pages_or_slides=1,
            metadata={"filename": filename, "format": "txt"}
        )
