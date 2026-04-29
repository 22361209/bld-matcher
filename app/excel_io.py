from __future__ import annotations

from pathlib import Path
from string import ascii_uppercase

from openpyxl import load_workbook
import xlrd
from xlutils.copy import copy as copy_xls

from .matcher import ProductCatalog, split_codes


INQUIRY_HEADERS = {
    "name": {"物料名称", "产品名称", "名称", "ITEM", "PART"},
    "oe": {"OE号", "OE", "OE NO", "OE NO.", "OE号码"},
    "description": {"物料描述", "描述", "DESCRIPTION", "DESC"},
}


def _norm_header(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _find_inquiry_columns(sheet) -> tuple[int, dict[str, int]]:
    aliases = {
        _norm_header(alias): key
        for key, names in INQUIRY_HEADERS.items()
        for alias in names
    }
    for row_index in range(min(sheet.nrows, 20)):
        columns: dict[str, int] = {}
        for col_index in range(sheet.ncols):
            key = aliases.get(_norm_header(sheet.cell_value(row_index, col_index)))
            if key:
                columns[key] = col_index
        if "oe" in columns or "name" in columns:
            return row_index, columns
    raise ValueError("询价表没有找到可识别表头，需要包含 OE号 或 物料名称。")


def _column_label(index: int) -> str:
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = ascii_uppercase[remainder] + label
    return label


def preview_inquiry_columns(inquiry_path: Path, max_rows: int = 8, max_cols: int = 12) -> dict:
    if inquiry_path.suffix.lower() == ".xls":
        book = xlrd.open_workbook(inquiry_path, formatting_info=True, ignore_workbook_corruption=True)
        sheet = book.sheet_by_index(0)
        cols = min(sheet.ncols, max_cols)
        rows = []
        for row_index in range(min(sheet.nrows, max_rows)):
            rows.append([sheet.cell_value(row_index, col_index) for col_index in range(cols)])
        return {
            "sheet": sheet.name,
            "columns": [{"index": i, "label": _column_label(i)} for i in range(cols)],
            "rows": rows,
        }

    workbook = load_workbook(inquiry_path, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    cols = min(sheet.max_column, max_cols)
    rows = []
    for row_index in range(1, min(sheet.max_row, max_rows) + 1):
        rows.append([sheet.cell(row_index, col_index).value for col_index in range(1, cols + 1)])
    return {
        "sheet": sheet.title,
        "columns": [{"index": i, "label": _column_label(i)} for i in range(cols)],
        "rows": rows,
    }


def _norm_openpyxl_cell(value: object) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _find_xlsx_inquiry_columns(sheet) -> tuple[int, dict[str, int]]:
    aliases = {
        _norm_header(alias): key
        for key, names in INQUIRY_HEADERS.items()
        for alias in names
    }
    for row_index in range(1, min(sheet.max_row, 20) + 1):
        columns: dict[str, int] = {}
        for col_index in range(1, sheet.max_column + 1):
            key = aliases.get(_norm_openpyxl_cell(sheet.cell(row_index, col_index).value))
            if key:
                columns[key] = col_index
        if "oe" in columns or "name" in columns:
            return row_index, columns
    raise ValueError("询价表没有找到可识别表头，需要包含 OE号 或 物料名称。")


def _summary_row(row_number: int, inquiry_oe: object, inquiry_name: object, match) -> dict:
    parts = split_codes(inquiry_oe)
    match_note = ""
    if len(parts) > 1:
        if match and match.matched_codes:
            notes = [f"命中号码：{', '.join(match.matched_codes)}"]
            if match.unmatched_codes:
                notes.append(f"未命中号码：{', '.join(match.unmatched_codes)}")
            if " / " in match.bld_no:
                notes.append(f"命中 BLD：{match.bld_no}")
            match_note = "；".join(notes)
        elif not match:
            match_note = f"多个号码均未命中：{', '.join(parts)}"

    row = {
        "row": row_number,
        "oe": inquiry_oe,
        "name": inquiry_name,
        "bld_no": match.bld_no if match else "",
        "reason": match.reason if match else "未找到",
        "score": match.score if match else 0,
        "match_note": match_note,
        "matched_oe_codes": [],
        "unmatched_oe_codes": [],
    }
    if match and len(parts) > 1 and match.matched_codes:
        row["matched_oe_codes"] = list(match.matched_codes)
        row["unmatched_oe_codes"] = list(match.unmatched_codes)
    return row


def generate_xls_with_bld(
    inquiry_path: Path,
    output_path: Path,
    catalog: ProductCatalog,
    match_column: int | None = None,
    write_output: bool = True,
) -> dict:
    book = xlrd.open_workbook(inquiry_path, formatting_info=True, ignore_workbook_corruption=True)
    writable = copy_xls(book) if write_output else None
    summary = {"total": 0, "matched": 0, "unmatched": 0, "rows": []}

    for sheet_index in range(book.nsheets):
        source_sheet = book.sheet_by_index(sheet_index)
        target_sheet = writable.get_sheet(sheet_index) if writable else None
        if source_sheet.nrows == 0:
            continue

        if match_column is None:
            header_row, columns = _find_inquiry_columns(source_sheet)
        else:
            header_row = 0
            columns = {"oe": match_column}
        output_col = source_sheet.ncols
        if target_sheet:
            target_sheet.write(header_row, output_col, "BLD NO.")
            target_sheet.write(header_row, output_col + 1, "匹配说明")

        for row_index in range(header_row + 1, source_sheet.nrows):
            inquiry_name = source_sheet.cell_value(row_index, columns["name"]) if "name" in columns else ""
            inquiry_oe = source_sheet.cell_value(row_index, columns["oe"]) if "oe" in columns else ""
            inquiry_desc = source_sheet.cell_value(row_index, columns["description"]) if "description" in columns else ""
            if not str(inquiry_name).strip() and not str(inquiry_oe).strip():
                continue

            match = catalog.match(inquiry_name, inquiry_oe, inquiry_desc)
            summary["total"] += 1
            if match:
                if target_sheet:
                    target_sheet.write(row_index, output_col, match.bld_no)
                summary["matched"] += 1
                row_summary = _summary_row(row_index + 1, inquiry_oe, inquiry_name, match)
                if target_sheet:
                    target_sheet.write(row_index, output_col + 1, row_summary["match_note"])
                summary["rows"].append(row_summary)
            else:
                if target_sheet:
                    target_sheet.write(row_index, output_col, "")
                summary["unmatched"] += 1
                row_summary = _summary_row(row_index + 1, inquiry_oe, inquiry_name, None)
                if target_sheet:
                    target_sheet.write(row_index, output_col + 1, row_summary["match_note"])
                summary["rows"].append(row_summary)

    if writable:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writable.save(str(output_path))
    return summary


def generate_xlsx_with_bld(
    inquiry_path: Path,
    output_path: Path,
    catalog: ProductCatalog,
    match_column: int | None = None,
    write_output: bool = True,
) -> dict:
    workbook = load_workbook(inquiry_path)
    summary = {"total": 0, "matched": 0, "unmatched": 0, "rows": []}

    for sheet in workbook.worksheets:
        if sheet.max_row == 0:
            continue

        if match_column is None:
            header_row, columns = _find_xlsx_inquiry_columns(sheet)
        else:
            header_row = 1
            columns = {"oe": match_column + 1}
        output_col = sheet.max_column + 1
        if write_output:
            sheet.cell(header_row, output_col).value = "BLD NO."
            sheet.cell(header_row, output_col + 1).value = "匹配说明"

        for row_index in range(header_row + 1, sheet.max_row + 1):
            inquiry_name = sheet.cell(row_index, columns["name"]).value if "name" in columns else ""
            inquiry_oe = sheet.cell(row_index, columns["oe"]).value if "oe" in columns else ""
            inquiry_desc = sheet.cell(row_index, columns["description"]).value if "description" in columns else ""
            if not str(inquiry_name or "").strip() and not str(inquiry_oe or "").strip():
                continue

            match = catalog.match(inquiry_name, inquiry_oe, inquiry_desc)
            summary["total"] += 1
            if match:
                if write_output:
                    sheet.cell(row_index, output_col).value = match.bld_no
                summary["matched"] += 1
                row_summary = _summary_row(row_index, inquiry_oe, inquiry_name, match)
                if write_output:
                    sheet.cell(row_index, output_col + 1).value = row_summary["match_note"]
                summary["rows"].append(row_summary)
            else:
                if write_output:
                    sheet.cell(row_index, output_col).value = ""
                summary["unmatched"] += 1
                row_summary = _summary_row(row_index, inquiry_oe, inquiry_name, None)
                if write_output:
                    sheet.cell(row_index, output_col + 1).value = row_summary["match_note"]
                summary["rows"].append(row_summary)

    if write_output:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
    return summary


def generate_excel_with_bld(
    inquiry_path: Path,
    output_path: Path,
    catalog: ProductCatalog,
    match_column: int | None = None,
    write_output: bool = True,
) -> dict:
    suffix = inquiry_path.suffix.lower()
    if suffix == ".xls":
        return generate_xls_with_bld(inquiry_path, output_path.with_suffix(".xls"), catalog, match_column=match_column, write_output=write_output)
    if suffix == ".xlsx":
        return generate_xlsx_with_bld(inquiry_path, output_path.with_suffix(".xlsx"), catalog, match_column=match_column, write_output=write_output)
    raise ValueError("客户询价文件仅支持 .xls 或 .xlsx。")
