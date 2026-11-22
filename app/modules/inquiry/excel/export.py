from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
import xlrd

from app.matcher import ProductCatalog
from app.product_status import product_status_header_for_price_mode

from ..adjustments import InquiryAdjustment, apply_adjustment, ensure_adjustments_consumed, workbook_row_adjustment_key
from .analysis import analyze_xlsx_with_bld, annotate_row_summary_with_match_columns, summary_row
from .cleanup import trim_unused_row_dimensions
from .headerless_export import (
    copy_xls_for_export, prepend_xlsx_header, source_column_titles, update_xlsx_header_references,
)
from .header_rows import validate_header_rows, writable_xlsx_header, xls_manual_headers
from .output_style import match_column_fonts, write_result_row
from .output_columns import prepare_xls_output_columns, prepare_xlsx_output_columns, xls_source_columns, xlsx_source_cells
from .pricing import match_export_price, match_export_status, price_export_header
from .source_columns import SourceSheetColumns, xls_source_values
from .reader import (
    _combined_match_text,
    _customer_code_text,
    _find_inquiry_columns,
    _find_xlsx_inquiry_columns_from_rows,
    _manual_match_columns,
    _selected_match_row_is_header,
    _xls_cell_value,
    _xls_match_values,
    _xlsx_match_values,
)


def generate_xls_with_bld(
    inquiry_path: Path,
    output_path: Path,
    catalog: ProductCatalog,
    match_column: object = None,
    write_output: bool = True,
    price_mode: str = "none",
    exchange_rate: float | None = None,
    customer_code_column: int | None = None,
    adjustments: dict[str, InquiryAdjustment] | None = None,
    include_source_columns: bool = False,
    header_rows: dict[int, int] | None = None,
) -> dict:
    book = xlrd.open_workbook(
        str(inquiry_path),
        formatting_info=True,
        ragged_rows=True,
        ignore_workbook_corruption=True,
    )
    manual_match_columns = _manual_match_columns(match_column)
    manual_headers = xls_manual_headers(book, manual_match_columns, header_rows)
    writable = copy_xls_for_export(book, manual_headers) if write_output else None
    summary = {"total": 0, "matched": 0, "unmatched": 0, "rows": []}
    if include_source_columns and not write_output:
        summary["source_sheets"] = []
    consumed_adjustment_keys: set[str] = set()
    for sheet_index in range(book.nsheets):
        source_sheet = book.sheet_by_index(sheet_index)
        target_sheet = writable.get_sheet(sheet_index) if writable else None
        if source_sheet.nrows == 0 or (manual_match_columns and source_sheet.name not in manual_headers):
            continue

        if not manual_match_columns:
            header_row, columns = _find_inquiry_columns(source_sheet)
            selected_match_columns = [columns["oe"]]
        else:
            header_row = manual_headers[source_sheet.name]
            columns = {}
            selected_match_columns = manual_match_columns
        source = None
        if "source_sheets" in summary:
            headers = xls_source_values(source_sheet, header_row, book.datemode) if header_row is not None else ()
            unnamed_match = selected_match_columns[0] if header_row is None and len(selected_match_columns) == 1 else None
            source = SourceSheetColumns(sheet_index + 1, source_sheet.name, headers, unnamed_match_column=unnamed_match)
        row_offset = int(header_row is None)
        source_header = -1 if header_row is None else header_row
        price_header = price_export_header(price_mode)
        status_header = product_status_header_for_price_mode(price_mode) if price_header else ""
        output_col = 0
        if target_sheet:
            used = xls_source_columns(source_sheet)
            output_col = prepare_xls_output_columns(source_sheet, target_sheet, used, source_header, 4 if price_header else 2)
            if row_offset:
                for column, title in source_column_titles(used, selected_match_columns, base=0):
                    target_sheet.write(0, column, title)
        status_col = output_col + 2 if price_header else None
        note_col = output_col + 3 if status_header else (output_col + 2 if price_header else output_col + 1)
        if target_sheet:
            output_header = source_header + row_offset
            target_sheet.write(output_header, output_col, "BLD NO.")
            if price_header:
                target_sheet.write(output_header, output_col + 1, price_header)
            if status_header and status_col is not None:
                target_sheet.write(output_header, status_col, status_header)
            target_sheet.write(output_header, note_col, "匹配说明")

        for row_index in range(source_header + 1, source_sheet.nrows):
            output_row = row_index + row_offset
            inquiry_name = _xls_cell_value(source_sheet, row_index, columns["name"]) if "name" in columns else ""
            customer_product_code = (
                _customer_code_text(_xls_cell_value(source_sheet, row_index, customer_code_column))
                if customer_code_column is not None and customer_code_column < source_sheet.ncols
                else ""
            )
            match_values = _xls_match_values(
                source_sheet,
                row_index,
                selected_match_columns,
            )
            if (header_rows or {}).get(sheet_index + 1) != 0 and _selected_match_row_is_header(match_values):
                continue
            inquiry_oe = _combined_match_text(match_values)
            inquiry_desc = (
                _xls_cell_value(source_sheet, row_index, columns["description"]) if "description" in columns else ""
            )
            if not str(inquiry_name).strip() and not str(inquiry_oe).strip():
                continue

            match = catalog.match(inquiry_name, inquiry_oe, inquiry_desc)
            adjustment_key = workbook_row_adjustment_key(sheet_index + 1, row_index + 1)
            adjustment = (adjustments or {}).get(adjustment_key)
            match, tax_price_override, adjustment_note = apply_adjustment(
                catalog,
                match,
                adjustment,
            )
            if adjustment is not None:
                consumed_adjustment_keys.add(adjustment_key)
            summary["total"] += 1
            if match:
                if target_sheet:
                    target_sheet.write(output_row, output_col, match.bld_no)
                    if price_header:
                        price = match_export_price(match, price_mode, exchange_rate, tax_price_override)
                        target_sheet.write(
                            output_row,
                            output_col + 1,
                            "" if price is None else price,
                        )
                    if status_header and status_col is not None:
                        target_sheet.write(
                            output_row,
                            status_col,
                            match_export_status(match, price_mode),
                        )
                summary["matched"] += 1
                row_summary = annotate_row_summary_with_match_columns(
                    summary_row(row_index + 1, inquiry_oe, inquiry_name, match, customer_product_code),
                    match_values,
                    match,
                )
                if tax_price_override is not None:
                    row_summary["price_cny"] = float(tax_price_override)
                if adjustment_note:
                    row_summary["match_note"] = (
                        f"{row_summary['match_note']}；{adjustment_note}"
                        if row_summary.get("match_note")
                        else adjustment_note
                    )
            else:
                if target_sheet:
                    target_sheet.write(output_row, output_col, "")
                    if price_header:
                        target_sheet.write(output_row, output_col + 1, "")
                    if status_header and status_col is not None:
                        target_sheet.write(output_row, status_col, "")
                summary["unmatched"] += 1
                row_summary = summary_row(row_index + 1, inquiry_oe, inquiry_name, None, customer_product_code)
            row_summary["adjustment_key"] = adjustment_key
            if target_sheet:
                target_sheet.write(output_row, note_col, row_summary["match_note"])
            if source is not None:
                source.attach_row(row_summary, xls_source_values(source_sheet, row_index, book.datemode))
            summary["rows"].append(row_summary)
        if source is not None:
            source.append_to(summary["source_sheets"])

    ensure_adjustments_consumed(adjustments, consumed_adjustment_keys)
    if writable:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writable.save(str(output_path))
    return summary


def generate_xlsx_with_bld(
    inquiry_path: Path,
    output_path: Path,
    catalog: ProductCatalog,
    match_column: object = None,
    write_output: bool = True,
    price_mode: str = "none",
    exchange_rate: float | None = None,
    customer_code_column: int | None = None,
    adjustments: dict[str, InquiryAdjustment] | None = None,
    include_source_columns: bool = False,
    header_rows: dict[int, int] | None = None,
) -> dict:
    if not write_output:
        return analyze_xlsx_with_bld(
            inquiry_path,
            catalog,
            match_column=match_column,
            price_mode=price_mode,
            exchange_rate=exchange_rate,
            customer_code_column=customer_code_column,
            adjustments=adjustments,
            include_source_columns=include_source_columns,
            header_rows=header_rows,
        )

    workbook = load_workbook(inquiry_path)
    try:
        shifted_sheets = set()
        summary = {"total": 0, "matched": 0, "unmatched": 0, "rows": []}
        consumed_adjustment_keys: set[str] = set()
        manual_match_columns = _manual_match_columns(match_column)
        validate_header_rows(header_rows, len(workbook.worksheets), manual_match_columns)

        for sheet_number, sheet in enumerate(workbook.worksheets, start=1):
            last_data_rows, used_rows, first_rows = xlsx_source_cells(sheet)
            data_end_row = max(used_rows, default=1)

            override = (header_rows or {}).get(sheet_number)
            if not manual_match_columns:
                header_row, columns = _find_xlsx_inquiry_columns_from_rows(first_rows)
                selected_match_columns = [columns["oe"]]
            else:
                selected_match_columns = [column + 1 for column in manual_match_columns]
                detected = writable_xlsx_header(sheet, used_rows, manual_match_columns, override)
                if detected is None:
                    continue
                header_row = detected or None
                columns = {}
            price_header = price_export_header(price_mode)
            status_header = product_status_header_for_price_mode(price_mode) if price_header else ""
            headers = ["BLD NO."]
            if price_header:
                headers.extend([price_header, status_header])
            headers.append("匹配说明")
            output_col = prepare_xlsx_output_columns(sheet, last_data_rows, header_row or 1, data_end_row, len(headers))
            column_fonts = match_column_fonts(sheet, selected_match_columns)
            if header_row is not None:
                write_result_row(sheet, header_row, output_col, column_fonts, headers)

            for row_index in used_rows:
                if header_row is not None and row_index <= header_row:
                    continue
                inquiry_name = sheet.cell(row_index, columns["name"]).value if "name" in columns else ""
                customer_product_code = (
                    _customer_code_text(sheet.cell(row_index, customer_code_column + 1).value)
                    if customer_code_column is not None
                    else ""
                )
                match_values = _xlsx_match_values(sheet, row_index, selected_match_columns, last_data_rows.keys())
                if override != 0 and _selected_match_row_is_header(match_values):
                    continue
                inquiry_oe = _combined_match_text(match_values)
                inquiry_desc = sheet.cell(row_index, columns["description"]).value if "description" in columns else ""
                if not str(inquiry_name or "").strip() and not str(inquiry_oe or "").strip():
                    continue

                match = catalog.match(inquiry_name, inquiry_oe, inquiry_desc)
                adjustment_key = workbook_row_adjustment_key(sheet_number, row_index)
                adjustment = (adjustments or {}).get(adjustment_key)
                match, tax_price_override, adjustment_note = apply_adjustment(
                    catalog,
                    match,
                    adjustment,
                )
                if adjustment is not None:
                    consumed_adjustment_keys.add(adjustment_key)
                summary["total"] += 1
                if match:
                    summary["matched"] += 1
                    row_summary = annotate_row_summary_with_match_columns(
                        summary_row(row_index, inquiry_oe, inquiry_name, match, customer_product_code),
                        match_values,
                        match,
                    )
                    if tax_price_override is not None:
                        row_summary["price_cny"] = float(tax_price_override)
                    if adjustment_note:
                        row_summary["match_note"] = (
                            f"{row_summary['match_note']}；{adjustment_note}"
                            if row_summary.get("match_note")
                            else adjustment_note
                        )
                else:
                    summary["unmatched"] += 1
                    row_summary = summary_row(row_index, inquiry_oe, inquiry_name, None, customer_product_code)
                row_summary["adjustment_key"] = adjustment_key
                values = [row_summary["bld_no"]]
                if price_header:
                    values.extend([
                        match_export_price(match, price_mode, exchange_rate, tax_price_override),
                        match_export_status(match, price_mode),
                    ])
                values.append(row_summary["match_note"])
                write_result_row(
                    sheet, row_index, output_col, column_fonts, values,
                    price_format=("0" if price_mode == "net" else "0.00") if price_header else None,
                )
                summary["rows"].append(row_summary)
            if header_row is None:
                prepend_xlsx_header(sheet)
                shifted_sheets.add(sheet.title.casefold())
                for column, title in source_column_titles(last_data_rows, selected_match_columns, base=1):
                    write_result_row(sheet, 1, column, column_fonts, [title])
                write_result_row(sheet, 1, output_col, column_fonts, headers)
            trim_unused_row_dimensions(sheet, sheet.max_row)

        ensure_adjustments_consumed(adjustments, consumed_adjustment_keys)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        update_xlsx_header_references(workbook, shifted_sheets)
        workbook.save(output_path)
        return summary
    finally:
        workbook.close()


def generate_excel_with_bld(
    inquiry_path: Path,
    output_path: Path,
    catalog: ProductCatalog,
    match_column: object = None,
    write_output: bool = True,
    price_mode: str = "none",
    exchange_rate: float | None = None,
    customer_code_column: int | None = None,
    adjustments: dict[str, InquiryAdjustment] | None = None,
    include_source_columns: bool = False,
    header_rows: dict[int, int] | None = None,
) -> dict:
    suffix = inquiry_path.suffix.lower()
    generators = {".xls": generate_xls_with_bld, ".xlsx": generate_xlsx_with_bld}
    if suffix not in generators:
        raise ValueError("客户询价文件仅支持 .xls 或 .xlsx。")
    return generators[suffix](
        inquiry_path,
        output_path.with_suffix(suffix),
        catalog,
        match_column=match_column,
        write_output=write_output,
        price_mode=price_mode,
        exchange_rate=exchange_rate,
        customer_code_column=customer_code_column,
        adjustments=adjustments,
        include_source_columns=include_source_columns,
        header_rows=header_rows,
    )
