import io
from typing import List

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.processors.base import DocumentElement, ParsedDocument, UnsupportedDocumentError, markdown_table


def parse_pptx(data: bytes) -> ParsedDocument:
    try:
        presentation = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise UnsupportedDocumentError(f"Unreadable presentation: {exc}") from exc

    elements: List[DocumentElement] = []
    for number, slide in enumerate(presentation.slides, start=1):
        title_shape = slide.shapes.title
        title = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        parts: List[str] = []
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            parts.extend(_shape_text(shape))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                parts.append(f"Speaker notes: {notes}")
        body = "\n".join(p for p in parts if p.strip())
        text = "\n".join(x for x in (title, body) if x)
        if text:
            section = f"Slide {number}: {title}" if title else f"Slide {number}"
            elements.append(DocumentElement(text=text, section=section, page_number=number, kind="slide"))
    return ParsedDocument(elements=elements, file_type="pptx")


def _shape_text(shape) -> List[str]:
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        out: List[str] = []
        for child in shape.shapes:
            out.extend(_shape_text(child))
        return out
    if getattr(shape, "has_table", False) and shape.has_table:
        rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
        rows = [r for r in rows if any(r)]
        return [markdown_table(rows)] if rows else []
    if getattr(shape, "has_chart", False) and shape.has_chart:
        chart = shape.chart
        if chart.has_title and chart.chart_title.has_text_frame:
            return [f"Chart: {chart.chart_title.text_frame.text.strip()}"]
        return []
    if shape.has_text_frame:
        lines = []
        for paragraph in shape.text_frame.paragraphs:
            text = "".join(run.text for run in paragraph.runs).strip()
            if text:
                lines.append(("  " * paragraph.level) + f"- {text}" if paragraph.level or len(shape.text_frame.paragraphs) > 1 else text)
        return lines
    return []
