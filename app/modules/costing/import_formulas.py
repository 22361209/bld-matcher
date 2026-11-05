"""Map known spreadsheet formulas to explicit editable cost algorithms."""
from __future__ import annotations

from decimal import Decimal, localcontext
import re
from typing import Any

from .formatters import format_cost_money
from .import_values import FormulaUnavailable, TOLERANCE, WorkbookValues, decimal_value, number, text
from .line_identity import line_identity_changes
from .material_attributes import suggested_material_attribute


_NUMERIC_FIELDS = (
    "amount", "unit_price", "quantity", "weight", "processing_fee", "length", "width", "height", "strap_rate",
)


def _set_inputs(values: WorkbookValues, sheet: str, row: int, item: dict, fields: dict[str, str]) -> None:
    for field, column in fields.items():
        item[field] = values.numeric(sheet, f"{column}{row}")


def _known_formula(values: WorkbookValues, sheet: str, row: int, formula: str, item: dict) -> bool:
    normalized = formula.upper().replace("$", "").replace(" ", "")
    local = re.sub(rf"([A-Z]+){row}(?!\d)", r"\1@", normalized)
    if local in {"=I@*K@", "=K@*I@", "=I@*K@+O@", "=K@*I@+O@"}:
        item.update(mode="weight", quantity="1", processing_fee="0")
        _set_inputs(values, sheet, row, item, {"unit_price": "I", "weight": "K"})
        if "O@" in local:
            item["processing_fee"] = values.numeric(sheet, f"O{row}")
        return True
    if local in {"=I@*J@", "=J@*I@", "=J@*O@", "=O@*J@"}:
        item["mode"] = "unit"
        _set_inputs(values, sheet, row, item, {"unit_price": "O" if "O@" in local else "I", "quantity": "J"})
        return True
    if local in {"=I@*J@*K@", "=I@*K@*J@", "=(I@*K@+O@)*J@", "=I@*K@*J@+O@*J@"}:
        item.update(mode="weight", processing_fee="0")
        _set_inputs(values, sheet, row, item, {"unit_price": "I", "weight": "K", "quantity": "J"})
        if "O@" in local:
            item["processing_fee"] = values.numeric(sheet, f"O{row}")
        return True
    if sheet == "摆臂成本核算" and local in {"=K@*H6", "=H6*K@"}:
        item.update(mode="freight", weight=values.numeric(sheet, f"K{row}"), unit_price=values.numeric(sheet, "H6"))
        return True
    if local == "=(I@+(((L@+N@)*4+(M@+N@)*4+(L@+M@)*2)*H3)/100)/J@" and sheet == "摆臂成本核算":
        item.update(mode="package", strap_rate=values.numeric(sheet, "H3"))
        _set_inputs(values, sheet, row, item, {
            "unit_price": "I", "quantity": "J", "length": "L", "width": "M", "height": "N",
        })
        return True
    return False


def _mapped_total(item: dict) -> Decimal | None:
    def value(field: str, default: str | None = None) -> Decimal:
        parsed = decimal_value(item.get(field) if item.get(field) is not None else default)
        if parsed is None:
            raise FormulaUnavailable(f"缺少 {field}")
        return parsed
    mode = item["mode"]
    if mode in {"fixed", "adjustment"}:
        return decimal_value(item.get("amount"))
    if mode == "unit":
        return value("unit_price") * value("quantity")
    if mode == "weight":
        return (value("unit_price") * value("weight") + value("processing_fee", "0")) * value("quantity", "1")
    if mode == "freight":
        return value("unit_price") * value("weight")
    if mode == "package":
        quantity = value("quantity")
        if quantity <= 0:
            raise FormulaUnavailable("装箱数必须大于零")
        strap = value("strap_rate", "0")
        strap_cost = Decimal(0)
        if strap:
            length, width, height = value("length"), value("width"), value("height")
            strap_cost = ((length + height) * 4 + (width + height) * 4 + (length + width) * 2) * strap / 100
        return (value("unit_price") + strap_cost) / quantity
    return None


def parse_cost_item(values: WorkbookValues, sheet: str, row: int, amount_column: str) -> dict:
    def get(column: str) -> object:
        return values.cell(sheet, f"{column}{row}")
    raw_amount = get(amount_column)
    cached_amount = values.cell(sheet, f"{amount_column}{row}", cached=True)
    item: dict[str, Any] = {field: None for field in _NUMERIC_FIELDS}
    item.update({
        "category": text(get("D")), "name": text(get("E")),
        "part_code": text(get("G" if sheet == "摆臂成本核算" else "F")),
        "supplier": text(get("F")) if sheet == "摆臂成本核算" else "",
        "remark": text(get("P")) if sheet == "摆臂成本核算" else "",
        "mode": "fixed", "review_note": "", "source_info": values.snapshot(sheet, row),
    })
    source = item["source_info"]
    source.update(amount_cell=f"{amount_column}{row}", original_formula=raw_amount if text(raw_amount).startswith("=") else None)
    notes: list[str] = []
    computed: Decimal | None = None
    if isinstance(raw_amount, str) and raw_amount.startswith("="):
        try:
            mapped = _known_formula(values, sheet, row, raw_amount, item)
            if mapped:
                computed = _mapped_total(item)
                source["conversion"] = "editable_algorithm"
            else:
                refs = re.findall(r"\$?([A-Z]{1,3})\$?([1-9]\d*)", raw_amount.upper())
                globals_only = sheet == "摆臂成本核算" and all(col == "H" and int(r) in {1, 2, 3, 4, 5, 6} for col, r in refs)
                if refs and not globals_only:
                    raise FormulaUnavailable("特殊公式或跨行引用，已保留原缓存金额为固定快照")
                computed = values.resolve(sheet, f"{amount_column}{row}")
                # Fixed snapshots are editable inputs; keep the domain's 18-place
                # input precision while retaining the unrounded source result.
                source["arithmetic_result"] = number(computed)
                with localcontext() as context:
                    context.prec = 40
                    exponent = computed.as_tuple().exponent
                    snapshot = computed.quantize(Decimal("0.000000000000000001")) if isinstance(exponent, int) and exponent < -18 else computed
                    item["amount"] = number(snapshot)
                source["conversion"] = "arithmetic_snapshot"
                source["reference_values"] = {f"{c}{r}": values.numeric(sheet, f"{c}{r}") for c, r in refs}
        except FormulaUnavailable as exc:
            item.update({field: None for field in _NUMERIC_FIELDS})
            item.update(mode="fixed", amount=number(cached_amount))
            source["conversion"] = "unresolved_snapshot"
            notes.append(str(exc))
        saved = decimal_value(cached_amount)
        if computed is not None and saved is not None and abs(computed - saved) > TOLERANCE:
            notes.append(f"原表缓存金额 {format_cost_money(saved)} 与复算金额 {format_cost_money(computed)} 原值有差异，请核对")
            source["reconciliation_difference"] = number(computed - saved)
    else:
        item["amount"] = number(raw_amount)
        computed = decimal_value(raw_amount)
        source["conversion"] = "direct_amount"
        if raw_amount not in (None, "") and computed is None:
            notes.append("原表金额不是有效数字，请补充")
    negative_fields = [key for key in _NUMERIC_FIELDS if decimal_value(item.get(key)) is not None and Decimal(item[key]) < 0]
    if negative_fields:
        retained = computed if computed is not None else decimal_value(cached_amount)
        item.update({field: None for field in _NUMERIC_FIELDS})
        item.update(mode="adjustment", amount=number(retained))
        notes.append("原表含负数，已保留为调整／返还金额，请核对")
    if not item["name"]:
        notes.append("原表未填写部件名称，请补充")
    try:
        computed = _mapped_total(item)
    except FormulaUnavailable as exc:
        computed = None
        notes.append(str(exc))
    source["recomputed_amount"] = number(computed)
    if computed is None:
        notes.append("金额或计算输入缺失，请补充")
    item["review_note"] = "；".join(dict.fromkeys(notes))
    item.update(line_identity_changes(item))
    item["material_attribute"] = suggested_material_attribute(item)
    return item
