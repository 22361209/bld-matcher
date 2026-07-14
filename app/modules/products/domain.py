from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Mapping

from app.product_status import canonical_product_status


PRODUCT_STATUSES = frozenset({"active", "inactive", "all"})
MAX_COLUMN_FILTER_VALUES = 200
MAX_COLUMN_FILTER_VALUE_LENGTH = 256


class ProductFilterValidationError(ValueError):
    """Raised when a product column filter exceeds the public request contract."""


@dataclass(frozen=True, slots=True)
class ProductFilters:
    query: str = ""
    bld_query: str = ""
    oe_query: str = ""
    series_query: str = ""
    model_query: str = ""
    status: str = "active"
    brands: tuple[str, ...] = ()
    items: tuple[str, ...] = ()
    product_statuses: tuple[str, ...] = ()
    brand_blank: bool = False
    item_blank: bool = False
    product_status_blank: bool = False

    @property
    def include_inactive(self) -> bool:
        return self.status == "all"

    @property
    def only_inactive(self) -> bool:
        return self.status == "inactive"


@dataclass(frozen=True, slots=True)
class ProductRecord:
    id: int
    bld_no: str
    series: str
    item: str
    oe_no_1: str
    oe_no_2: str
    models: str
    price_cny: float | None
    product_status: str
    image_path: str
    image_path_2: str
    image_path_3: str
    image_path_4: str
    image_path_5: str
    drawing_path: str
    drawing_original_name: str
    drawing_updated_at: str
    active: bool
    source: str
    created_at: str
    updated_at: str

    def web_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "bld_no": self.bld_no,
            "series": self.series,
            "item": self.item,
            "oe_no_1": self.oe_no_1,
            "oe_no_2": self.oe_no_2,
            "models": self.models,
            "price_cny": self.price_cny,
            "product_status": self.product_status,
            "image_path": self.image_path,
            "image_path_2": self.image_path_2,
            "image_path_3": self.image_path_3,
            "image_path_4": self.image_path_4,
            "image_path_5": self.image_path_5,
            "drawing_path": self.drawing_path,
            "drawing_original_name": self.drawing_original_name,
            "drawing_updated_at": self.drawing_updated_at,
            "active": 1 if self.active else 0,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def api_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "bld_no": self.bld_no,
            "series": self.series,
            "item": self.item,
            "oe_numbers": [line for line in self.oe_no_1.splitlines() if line.strip()],
            "brand_numbers": [line for line in self.oe_no_2.splitlines() if line.strip()],
            "models": self.models,
            "price_cny": self.price_cny,
            "product_status": self.product_status,
            "active": self.active,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ProductStats:
    products: int
    active: int
    inactive: int
    aliases: int

    def as_dict(self) -> dict[str, int]:
        return {
            "products": self.products,
            "active": self.active,
            "inactive": self.inactive,
            "aliases": self.aliases,
        }


@dataclass(frozen=True, slots=True)
class ProductPage:
    records: list[ProductRecord]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class ProductFilterOption:
    value: str
    label: str
    count: int

    def web_payload(self) -> dict[str, object]:
        return {"value": self.value, "label": self.label, "count": self.count}


@dataclass(frozen=True, slots=True)
class ProductFilterOptions:
    brand: tuple[ProductFilterOption, ...]
    item: tuple[ProductFilterOption, ...]
    product_status: tuple[ProductFilterOption, ...]

    def web_payload(self) -> dict[str, list[dict[str, object]]]:
        return {
            "brand": [option.web_payload() for option in self.brand],
            "item": [option.web_payload() for option in self.item],
            "product_status": [option.web_payload() for option in self.product_status],
        }


def compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _filter_source_values(value: object) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return value
    return (value,)


def _selected_filter_values(
    value: object,
    *,
    kind: str,
    label: str,
) -> tuple[tuple[str, ...], bool]:
    source_values = list(_filter_source_values(value))
    if len(source_values) > MAX_COLUMN_FILTER_VALUES:
        raise ProductFilterValidationError(f"{label}筛选项最多选择 {MAX_COLUMN_FILTER_VALUES} 个。")

    selected: list[str] = []
    seen: set[str] = set()
    blank_selected = False
    for raw in source_values:
        if raw is None:
            continue
        raw_text = str(raw or "")
        if len(raw_text) > MAX_COLUMN_FILTER_VALUE_LENGTH:
            raise ProductFilterValidationError(
                f"{label}筛选项单项不能超过 {MAX_COLUMN_FILTER_VALUE_LENGTH} 个字符。"
            )
        if not raw_text.strip():
            blank_selected = True
            continue
        if kind == "item":
            text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
            normalized = "\n".join(part for line in text.split("\n") if (part := compact(line)))
        elif kind == "product_status":
            normalized = canonical_product_status(raw_text)
        else:
            normalized = compact(raw_text)
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(normalized)
    return tuple(selected), blank_selected


def _source_filter_value(source: Mapping[str, object], plural: str, singular: str) -> object:
    if plural in source:
        return source[plural]
    return source.get(singular)


def _explicit_blank_selected(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return compact(value).lower() in {"1", "true", "yes", "on"}


def build_product_filters(values: Mapping[str, object] | ProductFilters) -> ProductFilters:
    if isinstance(values, ProductFilters):
        source: Mapping[str, object] = {
            "query": values.query,
            "bld_query": values.bld_query,
            "oe_query": values.oe_query,
            "series_query": values.series_query,
            "model_query": values.model_query,
            "status": values.status,
            "brands": values.brands,
            "items": values.items,
            "product_statuses": values.product_statuses,
            "brand_blank": values.brand_blank,
            "item_blank": values.item_blank,
            "product_status_blank": values.product_status_blank,
        }
    else:
        source = values
    status = compact(source.get("status") or "active").lower()
    if status not in PRODUCT_STATUSES:
        status = "active"
    query = compact(source.get("query") or source.get("q"))
    bld_query = compact(source.get("bld_query") or source.get("bld"))
    oe_query = compact(source.get("oe_query") or source.get("oe"))
    if oe_query:
        bld_query = ""
    brands, brand_blank = _selected_filter_values(
        _source_filter_value(source, "brands", "brand"),
        kind="brand",
        label="品牌",
    )
    items, item_blank = _selected_filter_values(
        _source_filter_value(source, "items", "item"),
        kind="item",
        label="产品名称",
    )
    product_statuses, product_status_blank = _selected_filter_values(
        _source_filter_value(source, "product_statuses", "product_status"),
        kind="product_status",
        label="产品状态",
    )
    return ProductFilters(
        query=query,
        bld_query=bld_query,
        oe_query=oe_query,
        series_query=compact(source.get("series_query") or source.get("series")),
        model_query=compact(source.get("model_query") or source.get("model")),
        status=status,
        brands=brands,
        items=items,
        product_statuses=product_statuses,
        brand_blank=brand_blank or _explicit_blank_selected(source.get("brand_blank")),
        item_blank=item_blank or _explicit_blank_selected(source.get("item_blank")),
        product_status_blank=product_status_blank
        or _explicit_blank_selected(source.get("product_status_blank")),
    )


def validated_price_value(value: object) -> str:
    text = compact(value)
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("含税单价请输入数字，或留空。") from exc
    if number < 0:
        raise ValueError("含税单价不能小于 0。")
    return str(round(number, 2))
