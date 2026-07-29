from __future__ import annotations

from dataclasses import dataclass


CUSTOMER_STATUSES = frozenset({"active", "inactive"})


@dataclass(frozen=True, slots=True)
class Customer:
    id: int
    name: str
    sync_id: str
    code: str
    status: str
    owner_username: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CustomerContact:
    id: int
    customer_id: int
    name: str
    title: str
    role: str
    phone: str
    email: str
    wechat: str
    is_primary: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CustomerSummary:
    customer: Customer
    primary_contact: CustomerContact | None
    quote_count: int
    quoted_product_count: int
    latest_quote_date: str
    file_count: int


@dataclass(frozen=True, slots=True)
class CustomerQuoteSummary:
    quote_no: str
    quote_date: str
    line_count: int
    product_count: int
    currency: str
    quoted_by: str


class CustomerValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def clean_customer_name(value: object) -> str:
    return " ".join(str(value or "").split())


def clean_single_line(value: object, *, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise CustomerValidationError("customer.field_too_long", f"内容不能超过 {limit} 个字符。")
    return text


def clean_customer_status(value: object) -> str:
    status = clean_single_line(value, limit=20).lower()
    if status not in CUSTOMER_STATUSES:
        raise CustomerValidationError("customer.status_invalid", "客户状态无效。")
    return status
