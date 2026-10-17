from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.processors.base import DocumentElement, ParsedDocument

# Sizes in characters (~4 chars/token for English). A 1200-char target (~300 tokens) keeps chunks
# focused enough for precise retrieval while carrying a full paragraph or table row group;
# mistral-embed accepts up to 8k tokens, so the 1800-char hard cap is far from the model limit.
TARGET_CHARS = 1200
MAX_CHARS = 1800
OVERLAP_CHARS = 200
MIN_CHARS = 250

_SEPARATORS = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " "]


@dataclass
class TextChunk:
    content: str
    chunk_index: int
    section: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    context: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def embedding_text(self) -> str:
        return f"{self.context}\n\n{self.content}" if self.context else self.content


def _location(file_type: str, section: Optional[str], page_start: Optional[int], page_end: Optional[int], meta: Dict) -> str:
    parts = []
    if section:
        parts.append(section)
    if page_start and file_type == "pdf":
        parts.append(f"page {page_start}" if page_end in (None, page_start) else f"pages {page_start}-{page_end}")
    elif meta.get("rows") and section and "rows" not in section:
        parts.append(f"rows {meta['rows']}")
    return ", ".join(parts)


def chunk_document(parsed: ParsedDocument, title: str) -> List[TextChunk]:
    """Turns parsed elements into retrieval chunks with a contextual header for embedding."""
    pieces: List[DocumentElement] = []
    for element in parsed.elements:
        text = element.text.strip()
        if not text:
            continue
        if len(text) <= MAX_CHARS:
            pieces.append(DocumentElement(text, element.section, element.page_number, element.kind, element.metadata))
        else:
            for part in split_text(text):
                pieces.append(DocumentElement(part, element.section, element.page_number, element.kind, element.metadata))

    # Merge consecutive small text pieces of the same section (tables and slides stay separate).
    merged: List[Dict[str, Any]] = []
    for piece in pieces:
        last = merged[-1] if merged else None
        can_merge = (
            last is not None
            and piece.kind == "text"
            and last["kind"] == "text"
            and last["section"] == piece.section
            and len(last["text"]) < TARGET_CHARS
            and len(last["text"]) + len(piece.text) + 2 <= MAX_CHARS
        )
        if can_merge:
            last["text"] += "\n\n" + piece.text
            if piece.page_number:
                last["page_end"] = piece.page_number
        else:
            merged.append({
                "text": piece.text,
                "section": piece.section,
                "kind": piece.kind,
                "page_start": piece.page_number,
                "page_end": piece.page_number,
                "meta": dict(piece.metadata),
            })

    # Fold a too-small trailing text piece into its predecessor when they share a section.
    compact: List[Dict[str, Any]] = []
    for item in merged:
        prev = compact[-1] if compact else None
        if (
            prev is not None
            and prev["kind"] == "text" and item["kind"] == "table"
            and len(prev["text"]) < MIN_CHARS
            and prev["section"] == item["section"]
            and prev["page_start"] == item["page_start"]
        ):
            # A short caption/heading line belongs with the table it introduces.
            item["text"] = prev["text"] + "\n\n" + item["text"]
            compact[-1] = item
            continue
        if (
            prev is not None
            and item["kind"] == "text" and prev["kind"] == "text"
            and len(item["text"]) < MIN_CHARS
            and prev["section"] == item["section"]
            and len(prev["text"]) + len(item["text"]) + 2 <= MAX_CHARS + MIN_CHARS
        ):
            prev["text"] += "\n\n" + item["text"]
            prev["page_end"] = item["page_end"] or prev["page_end"]
        else:
            compact.append(item)

    chunks: List[TextChunk] = []
    for index, item in enumerate(compact):
        location = _location(parsed.file_type, item["section"], item["page_start"], item["page_end"], item["meta"])
        context = f"Document: {title}" + (f" | {location}" if location else "")
        chunks.append(
            TextChunk(
                content=item["text"],
                chunk_index=index,
                section=item["section"],
                page_start=item["page_start"],
                page_end=item["page_end"],
                context=context,
                metadata={k: v for k, v in item["meta"].items() if k in ("rows", "location")},
            )
        )
    return chunks


def split_text(text: str, target: int = TARGET_CHARS, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> List[str]:
    """Splits long text at the most natural boundary before `max_chars`, with overlap."""
    out: List[str] = []
    start, length = 0, len(text)
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            window = text[start:end]
            cut = -1
            for sep in _SEPARATORS:
                pos = window.rfind(sep, target // 2)
                if pos != -1:
                    cut = pos + len(sep)
                    break
            if cut > 0:
                end = start + cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= length:
            break
        next_start = end - overlap
        # Start the overlap at a word boundary.
        space = text.find(" ", next_start, end)
        start = space + 1 if space != -1 else max(next_start, start + 1)
    return out
