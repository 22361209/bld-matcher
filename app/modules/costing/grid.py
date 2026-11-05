"""Cost-list display values and column filters, independent of HTTP and storage."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext

from .domain import CostingValidationError


COLUMN_FILTER_FIELDS = (
    "code", "name", "kind", "calculated", "approved", "scrap", "net",
    "minimum_price", "suggested_price", "status",
)
EMPTY_FILTER_VALUE = "__empty__"
MONEY_FIELDS = {
    "calculated": "calculated_cost", "approved": "approved_cost", "scrap": "scrap_credit",
    "net": "net_cost", "minimum_price": "minimum_tax_price", "suggested_price": "suggested_tax_price",
}
LABELS = {"kind": {"arm": "控制臂", "ball": "悬挂球头"}, "status": {"ready": "资料完整", "draft": "待补充 / 核对"}}


def format_cost_money(value: object) -> str:
    if value is None or value == "":
        return "—"
    try:
        with localcontext() as context:
            context.prec = 64
            amount = Decimal(str(value))
            if not amount.is_finite():
                return "—"
            amount = amount.quantize(Decimal("0.000001"))
            if not amount:
                return "0"
            return format(amount, ",f").rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError):
        return "—"


def normalize_column_filters(values: Mapping[str, object] | None) -> dict[str, tuple[str, ...]]:
    if values is not None and not isinstance(values, Mapping):
        raise CostingValidationError("列筛选格式无效。", "column_filters")
    normalized: dict[str, tuple[str, ...]] = {}
    for field, raw_values in (values or {}).items():
        if field not in COLUMN_FILTER_FIELDS:
            raise CostingValidationError("列筛选字段无效。", "column_filters")
        sequence = raw_values if isinstance(raw_values, Sequence) and not isinstance(raw_values, str) else (raw_values,)
        if len(sequence) > 5000:
            raise CostingValidationError("列筛选选项过多。", field)
        selected: list[str] = []
        for value in sequence:
            if not isinstance(value, str) or len(value) > 600:
                raise CostingValidationError("列筛选值无效。", field)
            if value and value not in selected:
                selected.append(value)
        if selected:
            normalized[field] = tuple(selected)
    return normalized


def column_value(record: Mapping[str, object], field: str) -> str:
    if field in MONEY_FIELDS:
        displayed = format_cost_money(record.get(MONEY_FIELDS[field]))
        return EMPTY_FILTER_VALUE if displayed == "—" else displayed
    if field == "name":
        parts = (record.get("name"), record.get("models") or record.get("brand"))
        value = " / ".join(str(part) for part in parts if part)
    else:
        value = str(record.get(field) or "")
    return value or EMPTY_FILTER_VALUE


def matches_columns(record: Mapping[str, object], filters: Mapping[str, tuple[str, ...]]) -> bool:
    return all(column_value(record, field) in selected for field, selected in filters.items())


def column_filter_options(
    records: list[dict], filters: Mapping[str, tuple[str, ...]],
) -> dict[str, list[dict[str, object]]]:
    # Candidate values honor other columns, while retaining alternatives in their own column.
    counters: dict[str, Counter[str]] = {field: Counter() for field in COLUMN_FILTER_FIELDS}
    for record in records:
        values = {field: column_value(record, field) for field in COLUMN_FILTER_FIELDS}
        unmatched = {field for field, selected in filters.items() if values[field] not in selected}
        for field, counter in counters.items():
            if not unmatched or unmatched == {field}:
                counter[values[field]] += 1
    options: dict[str, list[dict[str, object]]] = {}
    for field, counter in counters.items():
        # Selected zero-count entries stay visible and can be deselected after another filter changes.
        for selected in filters.get(field, ()):
            counter.setdefault(selected, 0)
        options[field] = [
            {"value": value, "label": "（空）" if value == EMPTY_FILTER_VALUE else LABELS.get(field, {}).get(value, value),
             "count": count}
            for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))
        ]
    return options
