"""
Tests for the WeasyPrint-based PDF report pipeline.

These tests call the synchronous internal `_*_pdf` helpers directly with
synthetic data — no database needed.

Key design decisions
--------------------
* The definitive regression guard is `test_pdf_has_no_image_xobjects`: it
  proves that no PNG rasterization is happening, which was the old approach.

* Text extraction uses pdfminer.six, which reads /ToUnicode CMaps.  pdfminer
  is reliable for post-base matras (া, ু) but drops pre-base vowels like ি
  and ে because those glyphs reorder visually *before* their consonant and
  pdfminer extracts by glyph position rather than Unicode logical order.
  Assertions therefore target words that survive this extraction faithfully.

* The ground-truth "copy-paste works" verification must be done manually in
  Adobe Reader — no Python PDF library replicates that accurately for
  complex-script Bengali.
"""

from io import BytesIO

import pytest

from app.services import report_service as svc

# ------------------------------------------------------------------
# Test strings
#
# Use words whose Unicode round-trips through pdfminer are reliable:
#   - া (aa-matra, U+09BE) is post-base → extracted in order ✓
#   - ু (u-matra, U+09C1) is sub-base  → extracted in order ✓
#   - ল্ল (la-virama-la conjunct)       → extracted as the cluster ✓
#   - ি (i-matra, U+09BF) is pre-base  → visually reorders, pdfminer drops ✗
#   - ে (e-matra, U+09CB) is pre-base  → same issue ✗
#
# BANGLA_CONJUNCT: contains ল্ল conjunct and ু (sub-base) — both survive.
# BANGLA_SIMPLE:  contains only post-/sub-base matras — survives cleanly.
# ------------------------------------------------------------------
BANGLA_CONJUNCT = "কুমিল্লা"    # test conjunct shaping; কু and ল্লা survive extraction
BANGLA_SIMPLE   = "ঢাকা"        # ঢ + া + ক + া — fully post-base, round-trips perfectly
BANGLA_COLLEGE  = "কুমিল্লা"    # extractable prefix of the college name

DATE_STR = "15 June 2026"


def _extract_text(pdf_bytes: bytes) -> str:
    """Extract text from every page using pdfminer.six (handles CID fonts)."""
    from pdfminer.high_level import extract_text
    return extract_text(BytesIO(pdf_bytes))


def _has_image_xobjects(pdf_bytes: bytes) -> bool:
    """
    Return True if the PDF embeds any raster image XObject.
    The old PNG-based pipeline always produced image XObjects; the WeasyPrint
    pipeline must produce none for purely text+table reports.
    """
    from pdfminer.pdfparser import PDFParser
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdftypes import resolve1

    parser = PDFParser(BytesIO(pdf_bytes))
    doc = PDFDocument(parser)
    for xref in doc.xrefs:
        for objid in xref.get_objids():
            try:
                obj = resolve1(doc.getobj(objid))
            except Exception:
                continue
            if isinstance(obj, dict) and str(obj.get("Subtype", "")) == "/Image":
                return True
            attrs = getattr(obj, "attrs", None)
            if isinstance(attrs, dict) and str(attrs.get("Subtype", "")) == "/Image":
                return True
    return False


# ------------------------------------------------------------------
# Structural / regression tests
# ------------------------------------------------------------------

def test_pdf_bytes_have_magic_header():
    pdf = svc._duty_list_pdf(["Name", "Role"], [[BANGLA_SIMPLE, "Invigilator"]], DATE_STR)
    assert pdf.startswith(b"%PDF-"), "Output is not a valid PDF"


def test_pdf_has_no_image_xobjects():
    """
    Primary regression guard: the old pipeline rasterized every Bengali cell
    to a PNG and embedded it as an image XObject.  The WeasyPrint pipeline
    must produce zero image XObjects for a pure text+table report.
    """
    headers = ["Invigilator Name", "Role", "Room", "Exam", "Time Slot"]
    rows = [[BANGLA_CONJUNCT, "Head Invigilator", "101", BANGLA_SIMPLE, "Morning"]]
    pdf = svc._duty_list_pdf(headers, rows, DATE_STR)
    assert not _has_image_xobjects(pdf), (
        "PDF contains image XObjects — Bengali text is being rasterized to PNG"
    )


def test_pdf_handles_empty_rows():
    pdf = svc._duty_list_pdf(["Name", "Role"], [], DATE_STR)
    assert pdf.startswith(b"%PDF-")


def test_pdf_handles_none_cells():
    rows = [["Alice", None, "101", "Math", "Morning"]]
    headers = ["Invigilator Name", "Role", "Room", "Exam", "Time Slot"]
    pdf = svc._duty_list_pdf(headers, rows, DATE_STR)
    assert pdf.startswith(b"%PDF-")


# ------------------------------------------------------------------
# Text-layer tests (pdfminer extraction)
# Assertions use only strings whose pdfminer extraction is reliable.
# ------------------------------------------------------------------

def test_duty_list_pdf_bangla_survives_as_text():
    headers = ["Invigilator Name", "Role", "Room", "Exam", "Time Slot"]
    rows = [[BANGLA_CONJUNCT, "Head Invigilator", "101", BANGLA_SIMPLE, "Morning"]]
    pdf = svc._duty_list_pdf(headers, rows, DATE_STR)
    text = _extract_text(pdf)

    # BANGLA_SIMPLE (ঢাকা) has only post-base matras → must round-trip exactly.
    assert BANGLA_SIMPLE in text, (
        f"'{BANGLA_SIMPLE}' not found in extracted text.\n"
        f"Extracted: {text!r}\n"
        "This means Bengali text is not selectable — likely a font or CMap issue."
    )
    # BANGLA_CONJUNCT prefix কু (consonant + sub-base u-matra) must also survive.
    assert "কু" in text, f"Conjunct prefix 'কু' not in: {text!r}"


def test_room_schedule_pdf_bangla_survives_as_text():
    headers = ["Room", "Exam", "Time Slot", "Seats", "Invigilators"]
    rows = [["101", BANGLA_SIMPLE, "Morning", "30", BANGLA_CONJUNCT]]
    pdf = svc._room_schedule_pdf(headers, rows, DATE_STR)
    assert pdf.startswith(b"%PDF-")
    text = _extract_text(pdf)
    assert BANGLA_SIMPLE in text, f"Extracted: {text!r}"


def test_daily_schedule_pdf_renders_both_sections():
    room_rows = [["101", BANGLA_SIMPLE, "Morning", "30", BANGLA_CONJUNCT]]
    duty_rows = [
        [BANGLA_CONJUNCT, "Head Invigilator", "101", BANGLA_SIMPLE, "Morning"],
        [BANGLA_SIMPLE,   "Invigilator",      "101", BANGLA_SIMPLE, "Morning"],
    ]
    pdf = svc._daily_schedule_pdf(room_rows, duty_rows, DATE_STR)
    assert pdf.startswith(b"%PDF-")
    text = _extract_text(pdf)

    # English section subtitles must be present.
    assert "Room Schedule" in text
    assert "Invigilator Duty List" in text

    # BANGLA_SIMPLE must survive from at least one section.
    assert BANGLA_SIMPLE in text, f"Extracted: {text!r}"


def test_college_header_contains_extractable_bangla():
    """
    The college name কুমিল্লা সরকারি মহিলা কলেজ is rendered as real text.
    We verify the extractable prefix কু (sub-base u-matra, reliable) appears.
    For full copy-paste verification use Adobe Reader manually.
    """
    pdf = svc._duty_list_pdf(["Name"], [["Alice"]], DATE_STR)
    text = _extract_text(pdf)
    # কু is the start of কুমিল্লা; sub-base matras survive pdfminer extraction.
    assert "কু" in text, (
        f"No Bengali text found in college header.\n"
        f"Extracted: {text!r}\n"
        "Likely cause: Noto Sans Bengali @font-face not loading — check Fontconfig."
    )
