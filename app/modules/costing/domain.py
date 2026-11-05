from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext


MAX_VALUE = Decimal("1000000000000")
MAX_ITEMS = 500
MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MODE_INPUTS = {
    "fixed": ("amount",), "adjustment": ("amount",), "unit": ("unit_price", "quantity"),
    "weight": ("unit_price", "quantity", "weight", "processing_fee"), "freight": ("unit_price", "weight"),
    "package": ("unit_price", "quantity", "strap_rate", "length", "width", "height"),
}
MODES = frozenset(MODE_INPUTS)
COST_KIND_LABELS = {"arm": "控制臂", "ball": "悬挂球头"}
DEFAULT_MATERIAL_ATTRIBUTE = "自制"
MATERIAL_ATTRIBUTES = (DEFAULT_MATERIAL_ATTRIBUTE, "外购", "委外加工")
NUMERIC_FIELDS = (
    "amount", "unit_price", "quantity", "weight", "processing_fee",
    "length", "width", "height", "strap_rate",
)
FIELD_LABELS = {
    "amount": "金额", "unit_price": "单价", "quantity": "数量", "weight": "重量",
    "processing_fee": "加工费", "length": "长", "width": "宽", "height": "高",
    "strap_rate": "打包带单价", "approved_cost": "核定成本", "scrap_credit": "废料抵减额",
    "theoretical_scrap": "理论废料金额", "suggested_tax_price_override": "建议含税单价",
}


class CostingValidationError(ValueError):
    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = "costing.invalid"


class CostingConflict(RuntimeError):
    def __init__(self, message: str = "成本单已被修改或型号已存在，请刷新后重试。") -> None:
        super().__init__(message)
        self.message = message
        self.code = "costing.conflict"


class CostingNotFound(LookupError):
    def __init__(self, message: str = "成本单不存在。") -> None:
        super().__init__(message)
        self.message = message
        self.code = "costing.not_found"


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    exponent = value.as_tuple().exponent
    return format(value, "f").rstrip("0").rstrip(".") if isinstance(exponent, int) and exponent < 0 else format(value, "f")


def text(value: object, field: str, limit: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CostingValidationError(f"{field} 必须是文字。", field)
    result = value.strip()
    if len(result) > limit:
        raise CostingValidationError(f"{field} 最多允许 {limit} 个字符。", field)
    return result


def number(value: object, field: str, *, allow_negative: bool = False) -> Decimal | None:
    if value is None or value == "":
        return None
    label = FIELD_LABELS.get(field.split(".")[-1], field)
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise CostingValidationError(f"{label} 必须是数字。", field)
    if isinstance(value, str) and len(value) > 120:
        raise CostingValidationError(f"{label} 数值或精度超出范围。", field)
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise CostingValidationError(f"{label} 必须是数字。", field) from exc
    if not parsed.is_finite():
        raise CostingValidationError(f"{label} 必须是有限数字。", field)
    if not allow_negative and parsed < 0:
        raise CostingValidationError(f"{label} 不能为负数。", field)
    exponent = parsed.as_tuple().exponent
    if (parsed.copy_abs() > MAX_VALUE or not isinstance(exponent, int)
            or exponent < -18 or len(parsed.as_tuple().digits) > 31):
        raise CostingValidationError(f"{label} 数值或精度超出范围。", field)
    return parsed


def with_pricing(record: Mapping[str, object]) -> dict:
    """Derive sale prices for current or legacy records without changing their metadata."""
    if not isinstance(record, Mapping):
        raise CostingValidationError("成本单格式无效。")
    override = number(record.get("suggested_tax_price_override"), "suggested_tax_price_override")
    minimum = None
    raw_net = record.get("net_cost")
    if raw_net is not None and raw_net != "":
        try:
            # Calculated costs can contain more digits than manually entered values.
            net = Decimal(str(raw_net))
        except InvalidOperation as exc:
            raise CostingValidationError("抵减后成本格式无效。", "net_cost") from exc
        if not net.is_finite() or net.copy_abs() > MAX_VALUE * 2:
            raise CostingValidationError("抵减后成本超出允许范围。", "net_cost")
        if net >= 0:
            with localcontext() as context:
                context.prec = 64
                minimum = (net / Decimal("0.8") * Decimal("1.1")).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    suggested = override if override is not None else minimum
    return {
        **record,
        "minimum_tax_price": decimal_text(minimum) if minimum is not None else None,
        "suggested_tax_price": decimal_text(suggested) if suggested is not None else None,
        "suggested_tax_price_override": decimal_text(override) if override is not None else None,
    }


def source_snapshot(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CostingValidationError("来源资料格式无效。", "source_info")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded) > 128_000:
            raise CostingValidationError("来源资料过大。", "source_info")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CostingValidationError):
            raise
        raise CostingValidationError("来源资料格式无效。", "source_info") from exc


def reference_snapshot(value: object) -> dict:
    """A whole workbook's reference tables have a separate bounded byte budget."""
    if not isinstance(value, dict):
        raise CostingValidationError("参考资料格式无效。", "references")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_REFERENCE_BYTES:
            raise CostingValidationError("参考资料超过 8 MiB，请减少无关内容后导入。", "references")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CostingValidationError):
            raise
        raise CostingValidationError("参考资料格式无效。", "references") from exc


def _total(values: dict[str, Decimal | None], mode: str, issues: list[str]) -> Decimal | None:
    def need(key: str) -> Decimal:
        value = values[key]
        if value is None:
            label = "每箱装几只" if key == "quantity" and mode == "package" else FIELD_LABELS[key]
            issues.append(f"请填写{label}")
            return Decimal(0)
        return value

    if mode in {"fixed", "adjustment"}:
        result = need("amount")
    elif mode == "unit":
        result = need("unit_price") * need("quantity")
    elif mode == "weight":
        quantity = values["quantity"] if values["quantity"] is not None else Decimal(1)
        result = (need("unit_price") * need("weight") + (values["processing_fee"] or Decimal(0))) * quantity
    elif mode == "freight":
        result = need("unit_price") * need("weight")
    else:
        price, quantity, strap = need("unit_price"), need("quantity"), need("strap_rate")
        strap_length = Decimal(0)
        if strap:
            length, width, height = need("length"), need("width"), need("height")
            strap_length = (length + height) * 4 + (width + height) * 4 + (length + width) * 2
        if values["quantity"] is not None and quantity <= 0:
            raise CostingValidationError("每箱装几只必须大于 0。", "quantity")
        result = (price + strap_length * strap / 100) / quantity if quantity else Decimal(0)
    if issues:
        return None
    if abs(result) > MAX_VALUE:
        raise CostingValidationError("计算金额超出允许范围。", "items")
    return result


def _item_inputs(raw: object, index: int, previous_items: dict) -> tuple[dict, dict[str, Decimal | None]]:
    if not isinstance(raw, Mapping):
        raise CostingValidationError("成本明细必须是对象。", f"items.{index}")
    row: dict = {
        field: text(raw.get(field), f"items.{index}.{field}", 2000 if field in {"remark", "review_note"} else 240)
        for field in ("id", "category", "name", "part_code", "supplier", "remark", "review_note")
    }
    if "product_code" in raw:
        row["product_code"] = text(raw["product_code"], f"items.{index}.product_code")
    if "material_attribute" in raw:
        field = f"items.{index}.material_attribute"
        attribute = text(raw["material_attribute"], field)
        if attribute and attribute not in MATERIAL_ATTRIBUTES:
            raise CostingValidationError(f"物料属性可留空或选择{'、'.join(MATERIAL_ATTRIBUTES)}。", field)
        row["material_attribute"] = attribute
    mode = text(raw.get("mode", "fixed"), "mode")
    if mode not in MODES:
        raise CostingValidationError("不支持的计价方式。", f"items.{index}.mode")
    row["mode"] = mode
    prior = previous_items.get(row["id"])
    if prior is not None and "supplier" not in raw:
        row["supplier"] = prior.get("supplier", "")
    # Historical snapshots can omit or pad a mode. Older editors omitted every
    # numeric input for an unrecognized mode. Explicit blanks still clear a cell.
    if (prior and prior.get("mode") != mode and raw.get("mode") == prior.get("mode")
            and not any(key in raw for key in MODE_INPUTS[mode])):
        raw = {**{key: prior.get(key) for key in MODE_INPUTS[mode]}, **raw}
    values = {
        field: number(raw.get(field), f"items.{index}.{field}", allow_negative=mode == "adjustment" and field == "amount")
        for field in NUMERIC_FIELDS
    }
    row["source_info"] = source_snapshot(raw.get("source_info"))
    return row, values


def _review_notes(item: dict) -> list[str]:
    notes = [] if item.get("name") else ["请填写成本项名称"]
    if item.get("review_note"):
        notes.append(item["review_note"])
    return notes


def _replace_review_notes(issues: list[str], old: list[str], new: list[str], prefix: str = "") -> list[str]:
    # Manual notes follow calculated warnings. Remove only their own occurrences
    # in case a note has exactly the same wording as a numeric warning.
    remaining = list(reversed(issues))
    for note in reversed(old):
        if prefix + note in remaining:
            remaining.remove(prefix + note)
    return list(reversed(remaining)) + [prefix + note for note in new]


def _metadata_update(before: dict, header: dict, summary: dict, row_inputs: list, raw_items: list) -> dict | None:
    """Preserve the locked record's financial snapshot when its calculation inputs match."""
    if len(before["items"]) != len(row_inputs) or any(
        number(before.get(key), key, allow_negative=key == "theoretical_scrap") != value
        for key, value in summary.items()
    ):
        return None
    items = []
    issues = before["issues"]
    for index, (prior, (row, values), raw) in enumerate(zip(before["items"], row_inputs, raw_items)):
        mode = row["mode"]
        if (row["id"] != prior["id"] or mode != text(prior.get("mode", "fixed"), "mode")
                or ("product_code" in row and row["product_code"] != text(prior.get("product_code"), "product_code"))):
            return None
        item = {**prior, **row, "source_info": prior.get("source_info", {})}
        for field in ("mode", "product_code"):
            if field in prior:
                item[field] = prior[field]
        for key, value in values.items():
            if key not in MODE_INPUTS[mode] and key not in raw:
                continue  # Older editors only submit fields active for the current mode.
            if number(prior.get(key), key, allow_negative=mode == "adjustment" and key == "amount") != value:
                if key in MODE_INPUTS[mode]:
                    return None
                item[key] = decimal_text(value) if value is not None else None
        old_notes, new_notes = _review_notes(prior), _review_notes(item)
        if old_notes != new_notes:
            item["issues"] = _replace_review_notes(prior["issues"], old_notes, new_notes)
            issues = _replace_review_notes(issues, old_notes, new_notes, f"第 {index + 1} 项：")
        items.append(item)
    status = before["status"] if issues == before["issues"] else ("draft" if issues else "ready")
    return {**before, **header, "items": items, "issues": issues, "status": status}


def calculate_cost(data: Mapping[str, object], *, previous: dict | None = None) -> dict:
    """Validate inputs and evaluate amounts, retaining financial history on metadata-only edits."""
    if not isinstance(data, Mapping):
        raise CostingValidationError("成本单格式无效。")
    result: dict = {
        field: text(data.get(field), field, 4000 if field == "remark" else 240)
        for field in ("code", "name", "brand", "models", "remark")
    }
    result["code"] = result["code"].upper()
    if not result["code"] or any(char.isspace() for char in result["code"]):
        raise CostingValidationError("请填写完整型号，型号中不能含空格。", "code")
    kind = text(data.get("kind", "arm"), "kind")
    if kind not in COST_KIND_LABELS:
        raise CostingValidationError(f"产品类型只支持{'或'.join(COST_KIND_LABELS.values())}。", "kind")
    result.update(kind=kind, source_info=source_snapshot(data.get("source_info")))
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list) or len(raw_items) > MAX_ITEMS:
        raise CostingValidationError(f"每张成本单最多允许 {MAX_ITEMS} 项明细。", "items")
    previous_items = {row["id"]: row for row in previous["items"]} if previous else {}
    row_inputs = [_item_inputs(raw, index, previous_items) for index, raw in enumerate(raw_items)]
    summary = {
        key: number(data.get(key), key, allow_negative=key == "theoretical_scrap")
        for key in ("approved_cost", "scrap_credit", "theoretical_scrap", "suggested_tax_price_override")
    }
    if previous is not None:
        preserved = _metadata_update(previous, result, summary, row_inputs, raw_items)
        if preserved is not None:
            return preserved
    with localcontext() as context:
        context.prec = 50
        items = []
        for row, values in row_inputs:
            row_issues: list[str] = []
            line_total = _total(values, row["mode"], row_issues)
            row.update({key: decimal_text(value) if value is not None else None for key, value in values.items()})
            row["line_total"] = decimal_text(line_total) if line_total is not None else None
            row["issues"] = row_issues + _review_notes(row)
            items.append(row)
        issues = [f"第 {index + 1} 项：{issue}" for index, row in enumerate(items) for issue in row["issues"]]
        if not items:
            issues.append("请添加成本明细")
        subtotal = sum((Decimal(row["line_total"]) for row in items if row["line_total"] is not None), Decimal(0))
        if abs(subtotal) > MAX_VALUE:
            raise CostingValidationError("成本合计超出允许范围。", "items")
        approved, credit, theoretical = summary["approved_cost"], summary["scrap_credit"], summary["theoretical_scrap"]
        if theoretical is not None and theoretical < 0:
            issues.append("理论废料金额为负，请核对毛重、净重及原表来源")
        calculated = subtotal if items and all(row["line_total"] is not None for row in items) else None
        effective = approved if approved is not None else calculated
        net = effective - (credit or Decimal(0)) if effective is not None else None
        if net is not None and net < 0:
            issues.append("废料抵减额超过采用成本，请核对")
        result.update(
            items=items, issues=issues, status="draft" if issues else "ready",
            approved_cost=decimal_text(approved) if approved is not None else None,
            scrap_credit=decimal_text(credit) if credit is not None else None,
            theoretical_scrap=decimal_text(theoretical) if theoretical is not None else None,
            known_subtotal=decimal_text(subtotal),
            calculated_cost=decimal_text(calculated) if calculated is not None else None,
            effective_cost=decimal_text(effective) if effective is not None else None,
            net_cost=decimal_text(net) if net is not None else None,
            suggested_tax_price_override=data.get("suggested_tax_price_override"),
        )
    return with_pricing(result)
