from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from app.platform.api_schemas import StrictApiModel


class InquiryAnalyzeRequest(StrictApiModel):
    numbers: list[str] = Field(default_factory=list, max_length=1000)
    text: str = Field(default="", max_length=5000)
    price_mode: Literal["none", "tax", "net", "usd"] = "tax"
    exchange_rate: float | None = Field(default=None, gt=0)
    rows_limit: int = Field(default=200, ge=0, le=1000)
    unmatched_limit: int = Field(default=100, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        if not self.numbers and not self.text.strip():
            raise ValueError("numbers 或 text 至少需要提供一项。")
        if self.price_mode == "usd" and self.exchange_rate is None:
            raise ValueError("price_mode=usd 时必须提供 exchange_rate。")
        return self


class InquiryExportRequest(InquiryAnalyzeRequest):
    source_name: str = Field(min_length=1, max_length=160)


class InquiryProduct(StrictApiModel):
    bld_no: str
    series: str
    item: str
    oe_no_1: str
    oe_no_2: str
    models: str
    price_cny: float | None
    product_status: str
    image_paths: list[str]


class InquiryRow(StrictApiModel):
    row: int | None
    original_number: str | int | float | None
    original_name: str | int | float | None
    matched: bool
    bld_no: str
    match_reason: str
    match_note: str
    score: int | float
    price_cny: float | None
    product_status: str
    export_price: int | float | None
    export_price_label: str
    matched_oe_codes: list[str]
    unmatched_oe_codes: list[str]
    product: InquiryProduct | None


class InquirySummary(StrictApiModel):
    total_rows: int
    matched_count: int
    unmatched_count: int
    returned_rows: int
    rows_truncated: bool
    invalid_items: list[str]
    price_mode: str


class ArtifactResponse(StrictApiModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    expires_at: str
    download_url: str


class InquiryData(StrictApiModel):
    mode: str
    summary: InquirySummary
    rows: list[InquiryRow]
    unmatched_list: list[str]
    artifact: ArtifactResponse | None


class InquiryEnvelope(StrictApiModel):
    api_version: str = "1"
    request_id: str
    data: InquiryData
    warnings: list[str] = Field(default_factory=list)
