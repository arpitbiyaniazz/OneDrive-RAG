import os
from typing import Optional
from app.processors.base import BaseDocumentParser
from app.processors.pdf import PDFParser
from app.processors.docx import DOCXParser
from app.processors.xlsx import XLSXParser
from app.processors.pptx import PPTXParser
from app.processors.txt import TXTParser


class DocumentParserFactory:
    """Factory creating the appropriate document processor based on file extension and MIME type."""

    _parsers = {
        "pdf": PDFParser,
        "docx": DOCXParser,
        "doc": DOCXParser,
        "gdoc": DOCXParser,
        "xlsx": XLSXParser,
        "xls": XLSXParser,
        "gsheet": XLSXParser,
        "pptx": PPTXParser,
        "ppt": PPTXParser,
        "gslides": PPTXParser,
        "txt": TXTParser,
        "md": TXTParser,
        "csv": TXTParser,
    }

    @classmethod
    def get_parser(cls, filename: str, mime_type: Optional[str] = None) -> BaseDocumentParser:
        ext = os.path.splitext(filename)[1].lower().replace(".", "")
        parser_cls = cls._parsers.get(ext)

        if not parser_cls and mime_type:
            if "pdf" in mime_type:
                parser_cls = PDFParser
            elif "word" in mime_type or "officedocument.wordprocessingml" in mime_type or "google-apps.document" in mime_type:
                parser_cls = DOCXParser
            elif "spreadsheet" in mime_type or "excel" in mime_type or "google-apps.spreadsheet" in mime_type:
                parser_cls = XLSXParser
            elif "presentation" in mime_type or "powerpoint" in mime_type or "google-apps.presentation" in mime_type:
                parser_cls = PPTXParser
            elif "text" in mime_type:
                parser_cls = TXTParser

        if not parser_cls:
            # Default to text parser fallback
            parser_cls = TXTParser

        return parser_cls()
