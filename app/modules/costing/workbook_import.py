"""Import the established cost workbook into detached, reviewable cost drafts.

This adapter never writes the source workbook or a database. Formula expressions
remain provenance, never executable application code. Only recognized formulas
become editable algorithms; other formulas remain explicitly reviewed snapshots.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from .import_formulas import parse_cost_item
from .import_references import attach_totals, read_pivot, read_references, scrap_by_code
from .import_values import WorkbookImportError, WorkbookValues, text


MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLS = 40
_SHEETS = {"摆臂成本核算", "球头成本核算", "透视表", "片件重量", "球座单价", "封板单价", "纸箱"}


def load_workbook_values(path: Path, *, sheets_to_read: set[str] = _SHEETS) -> WorkbookValues:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise WorkbookImportError("成本文件不能超过 30 MB")
    try:
        with ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise WorkbookImportError("成本文件展开内容过大，请清理无用行后重试")
        books = []
        try:
            for cached in (False, True):
                book = load_workbook(path, read_only=True, data_only=cached, keep_links=False)
                books.append(book)
            matrices = []
            for book in books:
                sheets = {}
                for sheet in book:
                    if sheet.title not in sheets_to_read:
                        continue
                    if sheet.max_row > MAX_ROWS or sheet.max_column > MAX_COLS:
                        raise WorkbookImportError(f"{sheet.title} 工作表范围过大，请清理空白格式后重试")
                    sheets[sheet.title] = list(sheet.values)
                matrices.append(sheets)
            return WorkbookValues(matrices[0], matrices[1])
        finally:
            for book in books:
                book.close()
    except (BadZipFile, KeyError, ValueError) as exc:
        if isinstance(exc, WorkbookImportError):
            raise
        raise WorkbookImportError("无法读取成本工作簿，请确认是有效的 xlsx 文件") from exc


def _header_row(values: WorkbookValues, sheet: str, amount_column: str) -> int:
    for row in range(1, min(15, len(values.raw[sheet])) + 1):
        if text(values.cell(sheet, f"A{row}")) == "本厂编码":
            if text(values.cell(sheet, f"E{row}")) != "部件名称" or text(values.cell(sheet, f"{amount_column}{row}")) != "金额":
                raise WorkbookImportError(f"{sheet} 表头不符合原成本表格式，未猜测列位置")
            return row
    raise WorkbookImportError(f"{sheet} 未找到本厂编码、部件名称和金额表头")


def _new_record(code: str, kind: str, sheet: str, filename: str, digest: str) -> dict:
    return {
        "code": code, "kind": kind, "name": "摆臂" if kind == "arm" else "球头",
        "brand": "", "models": "", "remark": "", "approved_cost": None,
        "scrap_credit": None, "theoretical_scrap": None, "items": [],
        "source_info": {"file_name": filename, "sha256": digest, "sheet": sheet, "excluded_rows": []},
    }


def _record_identity(record: dict, values: WorkbookValues, sheet: str, row: int) -> None:
    for field, column in (("brand", "B"), ("models", "C")):
        value = text(values.cell(sheet, f"{column}{row}"))
        if value and not record[field]:
            record[field] = value
        elif value and value != record[field]:
            variants = record["source_info"].setdefault("identity_variants", {})
            variants.setdefault(field, [])
            if value not in variants[field]:
                variants[field].append(value)


def parse_cost_workbook(path: Path) -> dict:
    """Return records, warnings, summary and reference snapshots; never mutate data."""
    path = Path(path)
    values = load_workbook_values(path)
    if not any(sheet in values.raw for sheet in ("摆臂成本核算", "球头成本核算")):
        raise WorkbookImportError("未找到摆臂成本核算或球头成本核算工作表")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    warnings: list[str] = []
    grouped: dict[str, dict] = {}
    stats: Counter = Counter()
    for sheet, kind, amount_column in (("摆臂成本核算", "arm", "H"), ("球头成本核算", "ball", "G")):
        if sheet not in values.raw:
            continue
        first_row = _header_row(values, sheet, amount_column) + 1
        for row in range(first_row, len(values.raw[sheet]) + 1):
            cells = values.raw[sheet][row - 1]
            if not any(value is not None for value in cells):
                continue
            code = text(values.cell(sheet, f"A{row}")).upper()
            if not code:
                warnings.append(f"{sheet} 第 {row} 行没有型号，未导入")
                stats["rows_without_code"] += 1
                continue
            if code in {"总计", "合计", "小计"}:
                stats["skipped_subtotals"] += 1
                continue
            if code in grouped and grouped[code]["kind"] != kind:
                raise WorkbookImportError(f"型号 {code} 同时出现在摆臂和球头成本表，请先确认型号归属")
            record = grouped.setdefault(code, _new_record(code, kind, sheet, path.name, digest))
            _record_identity(record, values, sheet, row)
            raw_amount = values.cell(sheet, f"{amount_column}{row}")
            label = text(values.cell(sheet, f"E{row}"))
            is_subtotal = bool(re.fullmatch(
                rf"=\s*(?:SUBTOTAL\(\s*(?:9|109)\s*,|SUM\()\s*\$?{amount_column}\$?\d+\s*:\s*\$?{amount_column}\$?\d+\s*\)",
                text(raw_amount), re.IGNORECASE,
            ))
            if is_subtotal or label in {"总计", "合计", "小计"}:
                excluded = values.snapshot(sheet, row)
                excluded["reason"] = "subtotal"
                record["source_info"]["excluded_rows"].append(excluded)
                stats["skipped_subtotals"] += 1
                continue
            # A copied empty slot (only model/brand/category) is not a zero-cost item.
            meaningful = any(value is not None for value in cells[4:])
            if not meaningful:
                excluded = values.snapshot(sheet, row)
                excluded["reason"] = "empty_slot"
                record["source_info"]["excluded_rows"].append(excluded)
                stats["skipped_empty_slots"] += 1
                continue
            record["items"].append(parse_cost_item(values, sheet, row, amount_column))
    references = read_references(values)
    pivot = read_pivot(values, warnings)
    scraps = scrap_by_code(references)
    records = []
    for code, record in grouped.items():
        if not record["items"]:
            warnings.append(f"{code} 没有有效成本项目，未创建成本单")
            stats["empty_records"] += 1
            continue
        attach_totals(record, pivot.get(code), scraps, warnings)
        if record["source_info"].get("identity_variants"):
            note = "同型号明细存在不同车系或车型，已保留各行原值，请核对"
            record["items"][0]["review_note"] = "；".join(filter(None, [record["items"][0]["review_note"], note]))
            warnings.append(f"{code}：{note}")
        records.append(record)
    for code in pivot.keys() - grouped.keys():
        warnings.append(f"透视表型号 {code} 没有对应成本明细，未创建只有总价的成本单")
    if not records:
        raise WorkbookImportError("工作簿没有可导入的成本项目")
    items = [item for record in records for item in record["items"]]
    summary = {
        "records": len(records), "arms": sum(record["kind"] == "arm" for record in records),
        "balls": sum(record["kind"] == "ball" for record in records), "items": len(items),
        "review_items": sum(bool(item["review_note"]) for item in items),
        "review_records": sum(any(item["review_note"] for item in record["items"]) for record in records),
        "missing_amount_items": sum(item["source_info"]["recomputed_amount"] is None for item in items),
        "formula_differences": sum("reconciliation_difference" in item["source_info"] for item in items),
        "pivot_differences": sum("pivot_difference" in record["source_info"] for record in records),
        "skipped_subtotals": stats["skipped_subtotals"], "skipped_empty_slots": stats["skipped_empty_slots"],
        "rows_without_code": stats["rows_without_code"], "empty_records": stats["empty_records"],
    }
    return {"records": records, "warnings": warnings, "summary": summary, "references": references}


__all__ = ["parse_cost_workbook", "WorkbookImportError"]
