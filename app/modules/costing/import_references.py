"""Preserve the workbook's reference tables and its separate approved totals."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import re
from typing import Any

from .import_values import FormulaUnavailable, TOLERANCE, WorkbookValues, decimal_value, number, text


def _numeric_cell(values: WorkbookValues, sheet: str, cell: str) -> str | None:
    try:
        return values.numeric(sheet, cell)
    except FormulaUnavailable:
        return number(values.cell(sheet, cell, cached=True))


def read_references(values: WorkbookValues) -> dict:
    references: dict[str, list] = {}
    layouts = {
        "cartons": ("纸箱", {"code": "A", "name": "B", "unit_price": "C", "length": "D", "width": "E", "height": "F"}),
        "ball_seats": ("球座单价", {"code": "A", "name": "B", "models": "C", "unit_price": "D", "supplier": "E"}),
        "covers": ("封板单价", {"code": "A", "name": "B", "unit_price": "C"}),
    }
    numeric = {"unit_price", "length", "width", "height"}
    for kind, (sheet, fields) in layouts.items():
        references[kind] = []
        for row in range(2, len(values.raw.get(sheet, [])) + 1):
            if not any(values.raw[sheet][row - 1]):
                continue
            entry: dict[str, Any] = {
                field: _numeric_cell(values, sheet, f"{column}{row}") if field in numeric else text(values.cell(sheet, f"{column}{row}"))
                for field, column in fields.items()
            }
            entry["source_info"] = values.snapshot(sheet, row)
            references[kind].append(entry)
    references["rates"] = [
        {"name": text(values.cell("摆臂成本核算", f"D{row}")), "amount": _numeric_cell(values, "摆臂成本核算", f"H{row}"),
         "source_info": values.snapshot("摆臂成本核算", row)}
        for row in range(1, 7) if text(values.cell("摆臂成本核算", f"D{row}"))
    ]
    references["scrap"] = []
    for row in range(2, len(values.raw.get("片件重量", [])) + 1):
        code = text(values.cell("片件重量", f"A{row}"))
        if not code:
            continue
        entry = {"code": code, "name": text(values.cell("片件重量", f"B{row}")), "source_info": values.snapshot("片件重量", row)}
        for field, column in {"unit_price": "C", "gross_weight": "D", "net_weight": "E", "scrap_weight": "F", "amount": "H"}.items():
            entry[field] = _numeric_cell(values, "片件重量", f"{column}{row}")
        gross, net = decimal_value(entry["gross_weight"]), decimal_value(entry["net_weight"])
        notes = []
        if gross is not None and net is not None and net > gross:
            notes.append("净重高于毛重")
        if any(entry[field] is None for field in ("unit_price", "gross_weight", "net_weight", "amount")):
            notes.append("重量或价格资料不完整")
        if decimal_value(entry["amount"]) is not None and Decimal(entry["amount"]) < 0:
            notes.append("理论废料额为负数")
        entry["review_note"] = "；".join(notes)
        references["scrap"].append(entry)
    return references


def read_pivot(values: WorkbookValues, warnings: list[str]) -> dict[str, dict]:
    records = {}
    sheet = "透视表"
    for row in range(4, len(values.raw.get(sheet, [])) + 1):
        code = text(values.cell(sheet, f"A{row}")).upper()
        if not code or code in {"总计", "合计", "(空白)"}:
            continue
        if code in records:
            warnings.append(f"透视表型号 {code} 重复，未自动选取核定成本")
            records[code]["ambiguous"] = True
            continue
        records[code] = {
            "approved_cost": _numeric_cell(values, sheet, f"C{row}"),
            "scrap_credit": _numeric_cell(values, sheet, f"F{row}"),
            "theoretical_scrap": _numeric_cell(values, sheet, f"K{row}"),
            "pivot_detail_total": _numeric_cell(values, sheet, f"B{row}"),
            "series_code": text(values.cell(sheet, f"J{row}")),
            "source_info": values.snapshot(sheet, row),
        }
    return records


def attach_totals(record: dict, pivot: dict | None, scraps: dict, warnings: list[str]) -> None:
    notes: list[str] = []
    source = record["source_info"]
    known = sum((decimal_value(item["source_info"].get("recomputed_amount")) or Decimal(0) for item in record["items"]), Decimal(0))
    source["imported_known_subtotal"] = number(known)
    if pivot:
        source["pivot"] = pivot["source_info"]
        if pivot.get("ambiguous"):
            notes.append("透视表重复型号，核定成本及废料抵减需手工确认")
        else:
            for field in ("approved_cost", "scrap_credit", "theoretical_scrap"):
                candidate = decimal_value(pivot[field])
                if candidate is not None and candidate < 0:
                    notes.append(f"原表 {field} 为负值，保留在来源中，请确认采用金额")
                else:
                    record[field] = pivot[field]
        total = decimal_value(pivot["pivot_detail_total"])
        if total is not None and abs(known - total) > TOLERANCE:
            notes.append(f"明细复算已知小计 {number(known)} 与原透视汇总 {number(total)} 不同，请核对")
            source["pivot_difference"] = number(known - total)
        series = pivot["series_code"]
    else:
        series = ""
    if not series:
        match = re.search(r"\d{4}", record["code"])
        series = match[0] if match else ""
    matching_scrap = scraps.get(series, []) if record["kind"] == "arm" else []
    if matching_scrap:
        source["scrap_reference_rows"] = matching_scrap
        amounts = [decimal_value(entry["amount"]) for entry in matching_scrap]
        source["scrap_known_total"] = number(sum((amount for amount in amounts if amount is not None), Decimal(0)))
        if pivot and pivot["theoretical_scrap"] is not None:
            for entry in matching_scrap:
                if entry["review_note"]:
                    notes.append(f"片件重量第 {entry['source_info']['row']} 行：{entry['review_note']}")
            theory = decimal_value(pivot["theoretical_scrap"])
            if all(amount is not None for amount in amounts) and theory is not None:
                difference = sum((amount for amount in amounts if amount is not None), Decimal(0)) - theory
                source["theoretical_scrap_difference"] = number(difference)
                if abs(difference) > TOLERANCE:
                    notes.append("片件废料额合计与原表采用的理论废料额不同，请核对")
    if notes:
        source["review_notes"] = list(dict.fromkeys(notes))
        warnings.extend(f"{record['code']}：{note}" for note in source["review_notes"])
        first = record["items"][0]
        first["review_note"] = "；".join(filter(None, [first["review_note"], *source["review_notes"]]))


def scrap_by_code(references: dict) -> dict:
    grouped = defaultdict(list)
    for entry in references["scrap"]:
        grouped[entry["code"]].append(entry)
    return grouped
