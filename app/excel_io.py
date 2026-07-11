"""Compatibility facade for inquiry workbook processing."""

from app.modules.inquiry.excel.analysis import analyze_xlsx_with_bld
from app.modules.inquiry.excel.cleanup import (
    POLLUTED_WORKSHEET_ROW_GAP,
    RISKY_XLSX_CLEANUP_PREFIXES,
    ROW_DIMENSION_CLEANUP_SLACK,
    WorkbookCleanupResult,
    sanitize_inquiry_workbook_if_needed,
)
from app.modules.inquiry.excel.export import (
    generate_excel_with_bld,
    generate_xls_with_bld,
    generate_xlsx_with_bld,
)
from app.modules.inquiry.excel.pricing import PRICE_EXPORT_MODES
from app.modules.inquiry.excel.reader import (
    INQUIRY_HEADERS,
    MANUAL_HEADER_ALIASES,
    preview_inquiry_columns,
)

__all__ = [
    "INQUIRY_HEADERS",
    "MANUAL_HEADER_ALIASES",
    "POLLUTED_WORKSHEET_ROW_GAP",
    "PRICE_EXPORT_MODES",
    "RISKY_XLSX_CLEANUP_PREFIXES",
    "ROW_DIMENSION_CLEANUP_SLACK",
    "WorkbookCleanupResult",
    "analyze_xlsx_with_bld",
    "generate_excel_with_bld",
    "generate_xls_with_bld",
    "generate_xlsx_with_bld",
    "preview_inquiry_columns",
    "sanitize_inquiry_workbook_if_needed",
]
