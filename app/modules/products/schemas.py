from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.platform.api_schemas import StrictApiModel


class ProductSearchQuery(StrictApiModel):
    q: str = ""
    bld: str = ""
    oe: str = ""
    series: str = ""
    model: str = ""
    status: Literal["active", "inactive", "all"] = "active"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ProductResponse(StrictApiModel):
    id: int
    bld_no: str
    series: str
    item: str
    oe_numbers: list[str]
    brand_numbers: list[str]
    models: str
    price_cny: float | None
    product_status: str
    active: bool
    updated_at: str


class ProductSearchData(StrictApiModel):
    products: list[ProductResponse]
    total: int
    limit: int
    offset: int


class ProductSearchEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: ProductSearchData
    warnings: list[str] = Field(default_factory=list)
