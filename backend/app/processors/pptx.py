import io
from pptx import Presentation
from typing import List
from app.processors.base import BaseDocumentParser, DocumentElement, ParsedDocument


class PPTXParser(BaseDocumentParser):
    """Parses PowerPoint .pptx presentations, extracting slide titles, body text, and notes."""

    def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        elements: List[DocumentElement] = []
        prs = Presentation(io.BytesIO(file_bytes))
        total_slides = len(prs.slides)

        for slide_idx, slide in enumerate(prs.slides):
            slide_num = slide_idx + 1
            slide_title = ""
            slide_texts: List[str] = []

            # Extract title if present
            if slide.shapes.title and slide.shapes.title.text:
                slide_title = slide.shapes.title.text.strip()

            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text.strip()
                        if text and text != slide_title:
                            slide_texts.append(text)

            # Extract speaker notes if any
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
                if notes_text:
                    slide_texts.append(f"Speaker Notes: {notes_text}")

            content_lines = []
            if slide_title:
                content_lines.append(f"### Slide {slide_num}: {slide_title}")
            else:
                content_lines.append(f"### Slide {slide_num}")

            if slide_texts:
                content_lines.extend(slide_texts)

            slide_content = "\n".join(content_lines)
            elements.append(
                DocumentElement(
                    text=slide_content,
                    page_number=slide_num,
                    section=f"Slide {slide_num}: {slide_title}" if slide_title else f"Slide {slide_num}",
                    metadata={
                        "slide_number": slide_num,
                        "slide_title": slide_title,
                        "total_slides": total_slides,
                    }
                )
            )

        return ParsedDocument(
            elements=elements,
            file_type="pptx",
            total_pages_or_slides=total_slides,
            metadata={"filename": filename, "format": "pptx", "slide_count": total_slides}
        )
