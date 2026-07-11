from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph

from .document_values import _text


PDF_FONT = "STSong-Light"
PDF_ASCII_FONT = "Arial"
PDF_ASCII_FALLBACK_FONT = "Helvetica"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF_ASCII_FONT_CANDIDATES = (
    PROJECT_ROOT / "data" / "fonts" / "Arial.ttf",
    PROJECT_ROOT / "static" / "fonts" / "Arial.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/arial.ttf"),
)
_pdf_ascii_font_name = PDF_ASCII_FALLBACK_FONT


def _font_registered(name: str) -> bool:
    try:
        pdfmetrics.getFont(name)
        return True
    except KeyError:
        return False


def _register_pdf_fonts() -> str:
    global _pdf_ascii_font_name
    if not _font_registered(PDF_FONT):
        pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))

    if _font_registered(PDF_ASCII_FONT):
        _pdf_ascii_font_name = PDF_ASCII_FONT
        return _pdf_ascii_font_name

    for candidate in PDF_ASCII_FONT_CANDIDATES:
        if not candidate.exists():
            continue
        pdfmetrics.registerFont(TTFont(PDF_ASCII_FONT, str(candidate)))
        _pdf_ascii_font_name = PDF_ASCII_FONT
        return _pdf_ascii_font_name

    _pdf_ascii_font_name = PDF_ASCII_FALLBACK_FONT
    return _pdf_ascii_font_name


def _styles() -> dict[str, ParagraphStyle]:
    ascii_font = _register_pdf_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ContractTitle",
            parent=base["Title"],
            fontName=PDF_FONT,
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "body": ParagraphStyle(
            "ContractBody",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10.5,
            leading=15,
        ),
        "body_indent": ParagraphStyle(
            "ContractBodyIndent",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10.5,
            leading=15,
            firstLineIndent=21,
        ),
        "small": ParagraphStyle(
            "ContractSmall",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=9,
            leading=12,
        ),
        "right": ParagraphStyle(
            "ContractRight",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10,
            leading=14,
            alignment=TA_RIGHT,
        ),
        "supplier_detail": ParagraphStyle(
            "ContractSupplierDetail",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=10.5,
            leading=15,
        ),
        "table": ParagraphStyle(
            "ContractTable",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=6.8,
            leading=8.4,
        ),
        "table_center": ParagraphStyle(
            "ContractTableCenter",
            parent=base["BodyText"],
            fontName=PDF_FONT,
            fontSize=6.8,
            leading=8.4,
            alignment=TA_CENTER,
        ),
        "table_right": ParagraphStyle(
            "ContractTableRight",
            parent=base["BodyText"],
            fontName=ascii_font,
            fontSize=6.8,
            leading=8.4,
            alignment=TA_RIGHT,
        ),
    }


def _p(text: object, style: ParagraphStyle) -> Paragraph:
    def escape(segment: str) -> str:
        return segment.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    raw = _text(text)
    parts: list[str] = []
    buffer: list[str] = []
    ascii_mode: bool | None = None

    def flush() -> None:
        nonlocal buffer, ascii_mode
        if not buffer:
            return
        segment = "".join(buffer)
        escaped = escape(segment)
        if ascii_mode and segment.strip():
            parts.append(f'<font name="{_pdf_ascii_font_name}">{escaped}</font>')
        else:
            parts.append(escaped)
        buffer = []

    for char in raw:
        if char == "\n":
            flush()
            parts.append("<br/>")
            ascii_mode = None
            continue
        is_ascii = 32 <= ord(char) <= 126
        if ascii_mode is not None and is_ascii != ascii_mode:
            flush()
        ascii_mode = is_ascii
        buffer.append(char)
    flush()
    return Paragraph("".join(parts) or "&nbsp;", style)
