"""Cost-sheet stamping scrap and the source evidence for each selected weight."""
from __future__ import annotations

from decimal import Decimal, localcontext

from .values import MAX_VALUE, CostingValidationError, decimal_text, number, text


WEIGHT_SOURCE_FIELDS = {"kind", "product_code", "name", "model", "spec", "source_id", "pieces",
                        "gross_weight", "net_weight", "scrap_weight"}
SCRAP_HEADER_FIELDS = ("scrap_mode", "scrap_unit_price", "scrap_credit_override",
                      "scrap_complete", "scrap_known_weight", "scrap_known_amount")
SCRAP_ROW_FIELDS = ("weight_source", "scrap_expected", "scrap_weight", "scrap_total_weight", "scrap_issue")


def automatic_scrap_credit(theoretical: Decimal | None) -> Decimal:
    return max(Decimal(0), theoretical - 1) if theoretical is not None else Decimal(0)


def weight_snapshot(value: object) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) != WEIGHT_SOURCE_FIELDS:
        raise CostingValidationError("重量来源快照字段不完整。", "weight_source")
    source = {key: text(value[key], key, 500) for key in ("kind", "product_code", "name", "model", "spec", "source_id")}
    if source["kind"] not in ("stamping", "tube") or not source["source_id"] or not source["product_code"]:
        raise CostingValidationError("重量来源类型、编码或记录编号无效。", "weight_source")
    amounts = {key: number(value[key], key) for key in ("pieces", "gross_weight", "net_weight", "scrap_weight")}
    gross, net, scrap = amounts["gross_weight"], amounts["net_weight"], amounts["scrap_weight"]
    if not gross or (net is not None and (net <= 0 or net > gross)):
        raise CostingValidationError("来源下料重量或净重无效。", "weight_source")
    if source["kind"] == "tube":
        if any(amounts[key] is not None for key in ("pieces", "net_weight", "scrap_weight")):
            raise CostingValidationError("管件来源仅提供单件重量。", "weight_source")
    elif not amounts["pieces"] or (gross - net if net is not None else None) != scrap:
        raise CostingValidationError("冲压件下料只数或废料重量与净重不符。", "weight_source")
    return {**source, **{key: decimal_text(v) if v is not None else None for key, v in amounts.items()}}


def _known_stamping(row):
    return row["mode"] == "weight" and any(word in row["name"] for word in ("主板", "副板", "加强板", "冲压件", "冲压片", "冲压板"))


def _prepare_weight(row, values, raw, prior, *, auto):
    existing = prior.get("weight_source")
    supplied = raw.get("weight_source", existing)
    expected = raw.get("scrap_expected", False)
    if not isinstance(expected, bool):
        raise CostingValidationError("冲压件待核对标记必须为布尔值。", "scrap_expected")
    source = weight_snapshot(supplied) if supplied is not None else None
    if source:
        code = row.get("product_code", prior.get("product_code", ""))
        matches = str(source["product_code"]).casefold() == code.casefold() and row["mode"] == "weight"
        if supplied == existing and (not matches or number(source["gross_weight"], "weight") != values["weight"]):
            source = None
        elif not matches:
            raise CostingValidationError("请按当前行的产品编码和计价方式重新取重。", "weight_source")
        else:
            values["weight"] = number(source["gross_weight"], "weight")
    tracked = auto or source or existing or "scrap_expected" in raw or "scrap_expected" in prior
    if not tracked:
        return
    # A known stamp still needs its own net weight after detaching a stale source.
    # A verified tube supersedes this classification; it contributes no stamping scrap.
    if source:
        expected = source["kind"] == "stamping"
    else:
        expected = row["mode"] == "weight" and (expected or prior.get("scrap_expected", False) or _known_stamping(row))
    row.update(weight_source=source, scrap_expected=expected)
    if not auto:
        return
    each = Decimal(source["scrap_weight"]) if source and source["scrap_weight"] is not None else None
    qty = values["quantity"] if values["quantity"] is not None else Decimal(1)
    extended = each * qty if each is not None else None
    issue = ""
    if expected and each is None:
        if not row.get("product_code", prior.get("product_code")):
            issue = "冲压件缺产品编码，待取重核算废料"
        elif not source:
            issue = "冲压件重量来源待确认，废料待核算"
        else:
            issue = "冲压件缺净重，废料待核算"
    row.update(scrap_weight=decimal_text(each) if each is not None else None,
               scrap_total_weight=decimal_text(extended) if extended is not None else None, scrap_issue=issue)


def prepare_scrap(data, previous, raw_items, row_inputs, previous_items):
    mode = data.get("scrap_mode", "legacy")
    if mode not in ("auto", "legacy"):
        raise CostingValidationError("废料核算方式无效。", "scrap_mode")
    if previous and previous.get("scrap_mode") == "auto" and mode != "auto":
        raise CostingValidationError("当前成本单已启用废料核算，请使用手动实际额调整。", "scrap_mode")
    auto = mode == "auto"
    with localcontext() as context:
        # One result may multiply three 18-place inputs and add up to 500 rows.
        context.prec = 100
        for raw, (row, values) in zip(raw_items, row_inputs, strict=True):
            _prepare_weight(row, values, raw, previous_items.get(row["id"], {}), auto=auto)
        if not auto:
            return {}, {}
        price = number(data.get("scrap_unit_price"), "scrap_unit_price")
        if price is None:
            price = Decimal(2)
        override = number(data.get("scrap_credit_override"), "scrap_credit_override")
        rows = [row for row, _ in row_inputs]
        complete = not any(row["scrap_issue"] for row in rows)
        weight = sum((Decimal(row["scrap_total_weight"]) for row in rows if row["scrap_total_weight"] is not None), Decimal(0))
        known = weight * price
        if weight > MAX_VALUE or known > MAX_VALUE:
            raise CostingValidationError("冲压件废料合计超出允许范围。", "scrap_unit_price")
        theory = known if complete else None
        credit = override
        if credit is None:
            credit = automatic_scrap_credit(theory)
        return {
            "scrap_mode": mode, "scrap_unit_price": decimal_text(price),
            "scrap_credit_override": decimal_text(override) if override is not None else None,
            "scrap_complete": complete, "scrap_known_weight": decimal_text(weight), "scrap_known_amount": decimal_text(known),
        }, {"theoretical_scrap": theory, "scrap_credit": credit}
