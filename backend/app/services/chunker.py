from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from app.processors.base import DocumentElement, ParsedDocument


@dataclass
class TextChunk:
    """Represents an indexed chunk with attached metadata."""
    content: str
    chunk_index: int
    page_number: Optional[int] = None
    section: Optional[str] = None
    metadata: Dict[str, Any] = None


class StructureAwareChunker:
    """
    Intelligently chunks documents while preserving structural metadata
    (page numbers, headings, sheet names, slide numbers, folder paths).
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        separators: Optional[List[str]] = None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", ". ", " ", ""]

    def chunk_document(
        self,
        parsed_doc: ParsedDocument,
        doc_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[TextChunk]:
        chunks: List[TextChunk] = []
        chunk_idx = 0
        doc_meta = doc_metadata or {}

        for element in parsed_doc.elements:
            text = element.text.strip()
            if not text:
                continue

            # If element text is smaller than chunk size, keep it as single chunk
            if len(text) <= self.chunk_size:
                meta = {**doc_meta, **element.metadata}
                chunks.append(
                    TextChunk(
                        content=text,
                        chunk_index=chunk_idx,
                        page_number=element.page_number,
                        section=element.section,
                        metadata=meta,
                    )
                )
                chunk_idx += 1
            else:
                # Split large element text using recursive boundaries
                sub_texts = self._split_text(text, self.chunk_size, self.chunk_overlap)
                for sub_t in sub_texts:
                    meta = {**doc_meta, **element.metadata}
                    chunks.append(
                        TextChunk(
                            content=sub_t,
                            chunk_index=chunk_idx,
                            page_number=element.page_number,
                            section=element.section,
                            metadata=meta,
                        )
                    )
                    chunk_idx += 1

        return chunks

    def _split_text(self, text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        """Splits text recursively using natural linguistic separators."""
        splits = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)
            if end < text_len:
                # Try finding natural break near the end
                found_break = False
                for sep in ["\n\n", "\n", ". ", " "]:
                    break_pos = text.rfind(sep, start + chunk_size // 2, end)
                    if break_pos != -1:
                        end = break_pos + len(sep)
                        found_break = True
                        break
                if not found_break:
                    end = min(start + chunk_size, text_len)

            chunk_text = text[start:end].strip()
            if chunk_text:
                splits.append(chunk_text)

            if end >= text_len:
                break
            start = max(start + 1, end - chunk_overlap)

        return splits
