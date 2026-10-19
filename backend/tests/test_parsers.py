import re

import pytest

from app.processors import UnsupportedDocumentError, parse_document
from app.services.chunker import MAX_CHARS, chunk_document, split_text
from tests.fixtures.corpus import build_corpus

CORPUS = build_corpus()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


@pytest.mark.parametrize("doc", [f for f in CORPUS if f.should_index], ids=lambda f: f.name)
def test_every_planted_fact_survives_parsing_and_chunking(doc):
    chunks = chunk_document(parse_document(doc.data, doc.name, doc.mime), doc.name)
    assert chunks
    text = [_norm(c.content) for c in chunks]
    for needle in doc.needles:
        assert any(_norm(needle.fact) in t for t in text), f"{needle.fact!r} lost from {doc.name}"
    assert all(len(c.content) <= MAX_CHARS + 300 for c in chunks)
    assert all(c.context.startswith(f"Document: {doc.name}") for c in chunks)


@pytest.mark.parametrize("doc", [f for f in CORPUS if not f.should_index], ids=lambda f: f.name)
def test_unindexable_files_are_rejected_with_a_reason(doc):
    with pytest.raises(UnsupportedDocumentError) as exc:
        parse_document(doc.data, doc.name, doc.mime)
    assert doc.reject_reason.lower() in exc.value.reason.lower()


def _doc(name):
    return next(f for f in CORPUS if f.name == name)


def test_docx_keeps_tables_in_reading_order_with_header():
    chunks = chunk_document(parse_document(_doc("Employee Handbook.docx").data, "h.docx"), "h.docx")
    sections = [c.section for c in chunks]
    table = next(c for c in chunks if "2800 EUR" in c.content)
    assert table.content.splitlines()[2].startswith("| Role |") or "| Role | Laptop budget | Monitor |" in table.content
    assert sections.index(table.section) < sections.index("Travel")  # table stays before the later section
    assert any(c.section == "Leave > Parental leave" for c in chunks)


def test_spreadsheet_chunks_repeat_headers_and_keep_sheet_and_rows():
    chunks = chunk_document(parse_document(_doc("Budget FY2026.xlsx").data, "b.xlsx"), "b.xlsx")
    quarterly = next(c for c in chunks if "731250" in c.content)
    assert quarterly.section == "Sheet: Quarterly"
    assert "| Department | Q1 | Q2 | Q3 | Q4 |" in quarterly.content
    assert "rows 2-4" in quarterly.context
    summary = next(c for c in chunks if "Priya Nair" in c.content)
    assert "rows 4-5" in summary.context  # real sheet row numbers despite the blank row


def test_pdf_strips_running_headers_and_dehyphenates():
    chunks = chunk_document(parse_document(_doc("Operations Report.pdf").data, "r.pdf"), "r.pdf")
    body = [c for c in chunks if c.section != "Page header and footer"]
    assert not any("ACME Logistics - Internal" in c.content for c in body)
    assert any("night-shift workers" in c.content for c in body)
    assert any(c.page_start == 2 and c.section == "Regional Results" for c in chunks)


def test_slide_notes_and_groups_are_attached_to_their_slide():
    chunks = chunk_document(parse_document(_doc("Strategy 2027.pptx").data, "s.pptx"), "s.pptx")
    slide2 = next(c for c in chunks if c.section == "Slide 2: Three pillars")
    assert "Speaker notes: The pilot launch date is 14 March 2027 in Lisbon." in slide2.content
    assert any("Northstar" in c.content and c.page_start == 4 for c in chunks)


def test_split_text_respects_limits_and_overlaps():
    text = " ".join(f"Sentence number {i} ends here." for i in range(400))
    parts = split_text(text)
    assert all(len(p) <= MAX_CHARS for p in parts)
    assert len(parts) > 3
    for left, right in zip(parts, parts[1:], strict=False):
        assert right.split(" ")[0] in left  # overlap carries context across the boundary
    assert "Sentence number 399 ends here." in parts[-1]


def test_small_paragraphs_are_merged_within_a_section():
    from app.processors.base import DocumentElement, ParsedDocument

    parsed = ParsedDocument([DocumentElement(f"Paragraph {i} " + "word " * 20, section="Intro") for i in range(10)], "txt")
    chunks = chunk_document(parsed, "doc.txt")
    assert len(chunks) < 5
    assert all(len(c.content) >= 200 for c in chunks)


def test_file_type_detection_explains_what_cannot_be_indexed():
    import io
    import zipfile

    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512
    with pytest.raises(UnsupportedDocumentError, match="Password-protected"):
        parse_document(ole + "EncryptedPackage".encode("utf-16-le") + b"\0" * 64, "locked.docx")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.bin", b"\0")
    with pytest.raises(UnsupportedDocumentError, match="xlsb"):
        parse_document(buf.getvalue(), "Book.xlsb")
    with pytest.raises(UnsupportedDocumentError) as exc:
        parse_document(b"\xff\xd8\xff\xe0" + b"\0" * 64, "photo.jpg")
    assert exc.value.needs_ocr and exc.value.file_type == "jpeg"
    with pytest.raises(UnsupportedDocumentError, match="Audio"):
        parse_document(b"RIFF\x24\0\0\0WAVEfmt " + b"\0" * 64, "voice.wav")


def test_scanned_pages_inside_a_text_pdf_are_flagged_for_ocr():
    import pymupdf

    from tests.fixtures.corpus import _pdf_scanned

    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(50, 60, 545, 800), "Signed agreement. " * 30, fontsize=11)
    doc.insert_pdf(pymupdf.open(stream=_pdf_scanned(), filetype="pdf"))
    parsed = parse_document(doc.tobytes(), "Agreement.pdf")
    assert parsed.ocr_pages == [2] and parsed.elements[0].page_number == 1
    with pytest.raises(UnsupportedDocumentError) as exc:
        parse_document(_pdf_scanned(), "Scan.pdf")
    assert exc.value.needs_ocr


def test_running_headers_are_removed_but_repeated_body_lines_are_kept():
    import pymupdf

    doc = pymupdf.open()
    for n in range(1, 5):
        page = doc.new_page()
        page.insert_text((50, 40), "Monthly statement - ACME Bank", fontsize=9)
        page.insert_textbox(pymupdf.Rect(50, 120, 545, 700), f"Balance on day {n}: {n * 1000}.50 EUR", fontsize=11)
        page.insert_text((290, 815), str(n), fontsize=9)
    parsed = parse_document(doc.tobytes(), "Statement.pdf")
    body = [e.text for e in parsed.elements if e.section != "Page header and footer"]
    assert all(f"Balance on day {n}: {n * 1000}.50 EUR" in "\n".join(body) for n in range(1, 5))
    assert not any("ACME Bank" in t for t in body)
    [running] = [e.text for e in parsed.elements if e.section == "Page header and footer"]
    assert running == "Monthly statement - ACME Bank"  # kept once; bare page numbers dropped
