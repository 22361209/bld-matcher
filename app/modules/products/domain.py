from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


PRODUCT_STATUSES = frozenset({"active", "inactive", "all"})


@dataclass(frozen=True, slots=True)
class ProductFilters:
    query: str = ""
    bld_query: str = ""
    oe_query: str = ""
    series_query: str = ""
    model_query: str = ""
    status: str = "active"

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


def compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def build_product_filters(values: Mapping[str, object] | ProductFilters) -> ProductFilters:
    if isinstance(values, ProductFilters):
        return values
    status = compact(values.get("status") or "active").lower()
    if status not in PRODUCT_STATUSES:
        status = "active"
    query = compact(values.get("query") or values.get("q"))
    bld_query = compact(values.get("bld_query") or values.get("bld"))
    oe_query = compact(values.get("oe_query") or values.get("oe"))
    if oe_query:
        bld_query = ""
    return ProductFilters(
        query=query,
        bld_query=bld_query,
        oe_query=oe_query,
        series_query=compact(values.get("series_query") or values.get("series")),
        model_query=compact(values.get("model_query") or values.get("model")),
        status=status,
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
