from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import zipfile


ROW_DIMENSION_CLEANUP_SLACK = 100
POLLUTED_WORKSHEET_ROW_GAP = 1000
RISKY_XLSX_CLEANUP_PREFIXES = (
    "xl/charts/",
    "xl/comments",
    "xl/drawings/",
    "xl/tables/",
    "xl/threadedComments/",
    "xl/vmlDrawings/",
)
_DIMENSION_RE = re.compile(rb'(<dimension\b[^>]*\bref=")([^"]+)(")')
_DIMENSION_ROW_RE = re.compile(r"\$?[A-Z]{1,4}\$?(\d+)", re.IGNORECASE)
_ROW_ATTR_RE = re.compile(rb'\br="(\d+)"')
_ROW_ELEMENT_RE = re.compile(
    rb"<row\b(?P<attrs_self>[^>]*)/>|<row\b(?P<attrs>[^>]*)>(?P<body>.*?)</row>",
    re.DOTALL,
)
_ROW_PAYLOAD_MARKERS = (b"<v", b"<f", b"<is", b"<t")


@dataclass(frozen=True)
class WorkbookCleanupResult:
    path: Path
    cleaned: bool = False
    original_path: Path | None = None
    message: str = ""
    details: dict[str, int] = field(default_factory=dict)


def trim_unused_row_dimensions(sheet, keep_until: int) -> None:
    if len(sheet.row_dimensions) <= keep_until + ROW_DIMENSION_CLEANUP_SLACK:
        return
    for row_index in [index for index in sheet.row_dimensions if index > keep_until]:
        del sheet.row_dimensions[row_index]


def _xlsx_row_index(attrs: bytes | None) -> int | None:
    if not attrs:
        return None
    match = _ROW_ATTR_RE.search(attrs)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _xlsx_row_has_payload(body: bytes | None) -> bool:
    if not body:
        return False
    return any(marker in body for marker in _ROW_PAYLOAD_MARKERS)


def _dimension_max_row(sheet_xml: bytes) -> int:
    match = _DIMENSION_RE.search(sheet_xml)
    if not match:
        return 0
    ref = match.group(2).decode("utf-8", errors="ignore")
    rows = [int(value) for value in _DIMENSION_ROW_RE.findall(ref)]
    return max(rows, default=0)


def _dimension_ref_with_max_row(ref: str, max_row: int) -> str:
    max_row = max(max_row, 1)
    parts = ref.split(":", 1)
    if len(parts) == 1:
        start = parts[0].strip() or "A1"
        col_match = re.match(r"(\$?[A-Z]{1,4})", start, re.IGNORECASE)
        col = col_match.group(1) if col_match else "A"
        return f"{col}1" if max_row == 1 else f"{col}1:{col}{max_row}"

    start, end = parts[0].strip() or "A1", parts[1].strip()
    col_match = re.match(r"(\$?[A-Z]{1,4})", end, re.IGNORECASE)
    end_col = col_match.group(1) if col_match else "A"
    return f"{start}:{end_col}{max_row}"


def _update_dimension_max_row(sheet_xml: bytes, max_row: int) -> tuple[bytes, bool]:
    def replace(match: re.Match[bytes]) -> bytes:
        original_ref = match.group(2).decode("utf-8", errors="ignore")
        updated_ref = _dimension_ref_with_max_row(original_ref, max_row)
        if updated_ref == original_ref:
            return match.group(0)
        return match.group(1) + updated_ref.encode("utf-8") + match.group(3)

    updated, count = _DIMENSION_RE.subn(replace, sheet_xml, count=1)
    return updated, bool(count and updated != sheet_xml)


def _cleanup_worksheet_xml(sheet_xml: bytes) -> tuple[bytes, dict[str, int]]:
    meaningful_max_row = 0
    max_row_tag = 0
    row_tag_count = 0

    for match in _ROW_ELEMENT_RE.finditer(sheet_xml):
        attrs = match.group("attrs") or match.group("attrs_self")
        row_index = _xlsx_row_index(attrs)
        if row_index is None:
            continue
        row_tag_count += 1
        max_row_tag = max(max_row_tag, row_index)
        if _xlsx_row_has_payload(match.group("body")):
            meaningful_max_row = max(meaningful_max_row, row_index)

    keep_until = max(meaningful_max_row, 1)
    declared_max_row = max(_dimension_max_row(sheet_xml), max_row_tag)
    if declared_max_row - keep_until < POLLUTED_WORKSHEET_ROW_GAP:
        return sheet_xml, {}

    removed_row_tags = 0

    def remove_empty_tail_rows(match: re.Match[bytes]) -> bytes:
        nonlocal removed_row_tags
        attrs = match.group("attrs") or match.group("attrs_self")
        row_index = _xlsx_row_index(attrs)
        if row_index and row_index > keep_until and not _xlsx_row_has_payload(match.group("body")):
            removed_row_tags += 1
            return b""
        return match.group(0)

    cleaned_xml = _ROW_ELEMENT_RE.sub(remove_empty_tail_rows, sheet_xml)
    cleaned_xml, dimension_updated = _update_dimension_max_row(cleaned_xml, keep_until)
    if not removed_row_tags and not dimension_updated:
        return sheet_xml, {}

    return cleaned_xml, {
        "effective_rows": keep_until,
        "declared_rows": declared_max_row,
        "removed_tail_rows": max(0, declared_max_row - keep_until),
        "removed_row_tags": removed_row_tags,
        "row_tag_count": row_tag_count,
    }


def sanitize_inquiry_workbook_if_needed(
    inquiry_path: Path,
    output_path: Path | None = None,
) -> WorkbookCleanupResult:
    inquiry_path = Path(inquiry_path)
    if inquiry_path.suffix.lower() != ".xlsx":
        return WorkbookCleanupResult(path=inquiry_path)

    try:
        with zipfile.ZipFile(inquiry_path, "r") as source:
            entries = source.infolist()
            names = [entry.filename for entry in entries]
            if any(name.startswith(RISKY_XLSX_CLEANUP_PREFIXES) for name in names):
                return WorkbookCleanupResult(path=inquiry_path)

            cleaned_entries: list[tuple[zipfile.ZipInfo, bytes]] = []
            changed_sheets = 0
            max_effective_rows = 0
            removed_tail_rows = 0
            removed_row_tags = 0
            for entry in entries:
                data = source.read(entry.filename)
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", entry.filename):
                    cleaned_data, details = _cleanup_worksheet_xml(data)
                    if details:
                        changed_sheets += 1
                        max_effective_rows = max(max_effective_rows, details["effective_rows"])
                        removed_tail_rows += details["removed_tail_rows"]
                        removed_row_tags += details["removed_row_tags"]
                    data = cleaned_data
                cleaned_entries.append((entry, data))
    except zipfile.BadZipFile:
        return WorkbookCleanupResult(path=inquiry_path)

    if not changed_sheets:
        return WorkbookCleanupResult(path=inquiry_path)

    cleaned_path = output_path or inquiry_path.with_name(f"{inquiry_path.stem}-cleaned{inquiry_path.suffix}")
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cleaned_path.with_name(f".{cleaned_path.name}.tmp")
    with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for entry, data in cleaned_entries:
            target.writestr(entry, data)
    temporary_path.replace(cleaned_path)

    details = {
        "cleaned_sheets": changed_sheets,
        "effective_rows": max_effective_rows,
        "removed_tail_rows": removed_tail_rows,
        "removed_row_tags": removed_row_tags,
    }
    message = (
        f"已自动清理 Excel 尾部空白格式：实际有效行 {max_effective_rows}，清理尾部空白格式约 {removed_tail_rows} 行。"
    )
    return WorkbookCleanupResult(
        path=cleaned_path,
        cleaned=True,
        original_path=inquiry_path,
        message=message,
        details=details,
    )
