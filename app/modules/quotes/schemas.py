from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.platform.api_schemas import StrictApiModel


Currency = Literal["CNY", "USD", "EUR"]
SourceType = Literal["manual", "wechat", "excel", "pdf", "image"]


class QuoteCreateRequest(StrictApiModel):
    customer_name: str = Field(min_length=1, max_length=300)
    bld_no: str | None = Field(default=None, max_length=100)
    product_model: str | None = Field(default=None, max_length=100)
    customer_product_code: str = Field(default="", max_length=200)
    price: Decimal | None = None
    tax_price: Decimal | None = None
    net_price: Decimal | None = None
    currency: Currency = "CNY"
    moq: int | None = Field(default=None, ge=0)
    quote_date: date | None = None
    quoted_by: str = Field(default="", max_length=200)
    source_type: SourceType = "manual"
    source_text: str = Field(default="", max_length=20_000)
    remark: str = Field(default="", max_length=5_000)
    on_behalf_of: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_identity_and_price(self):
        if not (self.bld_no or self.product_model):
            raise ValueError("bld_no 不能为空。")
        if self.price is None and self.tax_price is None and self.net_price is None:
            raise ValueError("tax_price 或 net_price 至少填写一个。")
        return self


class QuotePatchRequest(StrictApiModel):
    customer_name: str | None = Field(default=None, min_length=1, max_length=300)
    bld_no: str | None = Field(default=None, min_length=1, max_length=100)
    customer_product_code: str | None = Field(default=None, max_length=200)
    price: Decimal | None = None
    tax_price: Decimal | None = None
    net_price: Decimal | None = None
    currency: Currency | None = None
    moq: int | None = Field(default=None, ge=0)
    quote_date: date | None = None
    quoted_by: str | None = Field(default=None, max_length=200)
    source_type: SourceType | None = None
    source_text: str | None = Field(default=None, max_length=20_000)
    remark: str | None = Field(default=None, max_length=5_000)
    on_behalf_of: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def require_business_change(self):
        changed = set(self.model_fields_set) - {"on_behalf_of"}
        if not changed:
            raise ValueError("至少提交一个需要修改的报价字段。")
        return self


class QuoteListQuery(StrictApiModel):
    customer_name: str = Field(default="", max_length=300)
    bld_no: str = Field(default="", max_length=100)
    date_from: date | None = None
    date_to: date | None = None
    currency: Currency | None = None
    quoted_by: str = Field(default="", max_length=200)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to。")
        return self


class QuoteLatestQuery(StrictApiModel):
    customer_name: str = Field(min_length=1, max_length=300)
    bld_no: str = Field(min_length=1, max_length=100)


class QuoteResponse(StrictApiModel):
    id: int
    customer_name: str
    bld_no: str
    customer_product_code: str
    product_model: str
    price: float
    tax_price: float | None
    net_price: float | None
    currency: Currency
    moq: int | None
    quote_date: date
    quoted_by: str
    source_type: SourceType
    source_text: str
    remark: str
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class QuoteData(StrictApiModel):
    quote: QuoteResponse


class QuoteListData(StrictApiModel):
    quotes: list[QuoteResponse]
    total: int
    limit: int
    offset: int


class QuoteLatestData(StrictApiModel):
    quote: QuoteResponse | None


class QuoteEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: QuoteData
    warnings: list[str] = Field(default_factory=list)


class QuoteListEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: QuoteListData
    warnings: list[str] = Field(default_factory=list)


class QuoteLatestEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: QuoteLatestData
    warnings: list[str] = Field(default_factory=list)
