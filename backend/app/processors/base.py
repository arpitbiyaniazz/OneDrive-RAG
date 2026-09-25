from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DocumentElement:
    """Represents an atomic structural unit of a document (e.g. a page, paragraph, or slide)."""
    text: str
    page_number: Optional[int] = None
    section: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """Represents a fully parsed document with preserved structural hierarchy."""
    elements: List[DocumentElement]
    file_type: str
    total_pages_or_slides: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(e.text for e in self.elements if e.text.strip())


class BaseDocumentParser(ABC):
    """Abstract base class for all document format parsers."""

    @abstractmethod
    def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        """Parses raw document bytes and extracts text with structural metadata."""
        pass
