from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime


QUOTE_CURRENCIES = frozenset({"CNY", "USD", "EUR"})
QUOTE_SOURCE_TYPES = frozenset({"manual", "wechat", "excel", "pdf", "image"})


class QuoteValidationError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True, slots=True)
class QuoteRecord:
    id: int
    customer_name: str
    bld_no: str
    customer_product_code: str
    product_model: str
    price: float
    tax_price: float | None
    net_price: float | None
    currency: str
    moq: int | None
    quote_date: str
    quoted_by: str
    source_type: str
    source_text: str
    attachment_path: str
    remark: str
    version: int
    created_at: str
    updated_at: str

    def legacy_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "customer_name": self.customer_name,
            "bld_no": self.bld_no,
            "customer_product_code": self.customer_product_code,
            "product_model": self.product_model,
            "price": self.price,
            "tax_price": self.tax_price,
            "net_price": self.net_price,
            "currency": self.currency,
            "moq": self.moq,
            "quote_date": self.quote_date,
            "quoted_by": self.quoted_by,
            "source_type": self.source_type,
            "source_text": self.source_text,
            "attachment_path": self.attachment_path,
            "remark": self.remark,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def api_payload(self) -> dict[str, object]:
        payload = self.legacy_payload()
        payload.pop("attachment_path")
        return payload


@dataclass(frozen=True, slots=True)
class QuoteDraft:
    customer_name: str
    bld_no: str
    customer_product_code: str
    product_model: str
    price: float
    tax_price: float | None
    net_price: float | None
    currency: str
    moq: int | None
    quote_date: str
    quoted_by: str
    source_type: str
    source_text: str
    attachment_path: str
    remark: str
    created_at: str
    updated_at: str

    def storage_values(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in (
                "customer_name",
                "bld_no",
                "customer_product_code",
                "product_model",
                "price",
                "tax_price",
                "net_price",
                "currency",
                "moq",
                "quote_date",
                "quoted_by",
                "source_type",
                "source_text",
                "attachment_path",
                "remark",
                "created_at",
                "updated_at",
            )
        }


@dataclass(frozen=True, slots=True)
class QuoteFilters:
    customer_name: str = ""
    bld_no: str = ""
    date_from: str = ""
    date_to: str = ""
    currency: str = ""
    quoted_by: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "customer_name": self.customer_name,
            "bld_no": self.bld_no,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "currency": self.currency,
            "quoted_by": self.quoted_by,
        }


@dataclass(frozen=True, slots=True)
class QuoteStats:
    total: int
    customers: int
    models: int

    def as_dict(self) -> dict[str, int]:
        return {"total": self.total, "customers": self.customers, "models": self.models}


def compact_text(value: object) -> str:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()


def clean_multiline(value: object) -> str:
    text = "" if value is None else str(value)
    lines = [compact_text(line) for line in text.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _value(data: Mapping[str, object], field: str, existing: QuoteRecord | None, default: object = "") -> object:
    if field in data:
        return data.get(field)
    if existing is not None:
        return getattr(existing, field)
    return default


def _optional_price(value: object, field: str) -> float | None:
    text = compact_text(value)
    if not text:
        return None
    try:
        parsed = float(text)
    except (TypeError, ValueError) as exc:
        raise QuoteValidationError(f"quote.invalid_{field}", f"{field} 必须是数字。", field=field) from exc
    if not math.isfinite(parsed):
        raise QuoteValidationError(f"quote.invalid_{field}", f"{field} 必须是有限数字。", field=field)
    return round(parsed, 4)


def _moq(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise QuoteValidationError("quote.invalid_moq", "moq 必须是整数。", field="moq") from exc
    if parsed < 0:
        raise QuoteValidationError("quote.invalid_moq", "moq 不能小于 0。", field="moq")
    return parsed


def _record_date(value: object, *, now: datetime) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = compact_text(value)
    if not text:
        return now.strftime("%Y-%m-%d")
    for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise QuoteValidationError("quote.invalid_date", f"日期格式不正确：{text}", field="quote_date")


def build_quote_draft(
    data: Mapping[str, object],
    *,
    actor: str = "",
    existing: QuoteRecord | None = None,
    now: datetime | None = None,
) -> QuoteDraft:
    timestamp = now or datetime.now()
    timestamp_text = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    customer_name = clean_multiline(_value(data, "customer_name", existing))
    bld_default = _value(data, "product_model", existing)
    bld_no = compact_text(_value(data, "bld_no", existing, bld_default))
    if not customer_name:
        raise QuoteValidationError("quote.customer_required", "customer_name 不能为空。", field="customer_name")
    if not bld_no:
        raise QuoteValidationError("quote.bld_required", "bld_no 不能为空。", field="bld_no")

    explicit_price = data.get("price") if "price" in data else None
    tax_price_source = _value(data, "tax_price", existing, None)
    net_price_source = _value(data, "net_price", existing, None)
    if explicit_price not in (None, "") and "tax_price" not in data and "net_price" not in data:
        tax_price_source = explicit_price
    tax_price = _optional_price(tax_price_source, "tax_price")
    net_price = _optional_price(net_price_source, "net_price")
    if tax_price is None and net_price is None:
        raise QuoteValidationError(
            "quote.price_required",
            "tax_price 或 net_price 至少填写一个。",
            field="tax_price",
        )

    currency = compact_text(_value(data, "currency", existing, "CNY")).upper()
    if currency not in QUOTE_CURRENCIES:
        raise QuoteValidationError(
            "quote.invalid_currency",
            "currency 只允许 CNY/USD/EUR。",
            field="currency",
        )
    source_type = compact_text(_value(data, "source_type", existing, "manual")).lower()
    if source_type not in QUOTE_SOURCE_TYPES:
        raise QuoteValidationError(
            "quote.invalid_source_type",
            "source_type 只允许 manual/wechat/excel/pdf/image。",
            field="source_type",
        )

    return QuoteDraft(
        customer_name=customer_name,
        bld_no=bld_no,
        customer_product_code=compact_text(_value(data, "customer_product_code", existing)),
        product_model=bld_no,
        price=tax_price if tax_price is not None else float(net_price),
        tax_price=tax_price,
        net_price=net_price,
        currency=currency,
        moq=_moq(_value(data, "moq", existing, None)),
        quote_date=_record_date(_value(data, "quote_date", existing, None), now=timestamp),
        quoted_by=compact_text(_value(data, "quoted_by", existing, actor)),
        source_type=source_type,
        source_text=clean_multiline(_value(data, "source_text", existing)),
        attachment_path=compact_text(_value(data, "attachment_path", existing)),
        remark=clean_multiline(_value(data, "remark", existing)),
        created_at=existing.created_at if existing else timestamp_text,
        updated_at=timestamp_text,
    )


def build_quote_filters(data: Mapping[str, object]) -> QuoteFilters:
    currency = compact_text(data.get("currency")).upper()
    if currency and currency not in QUOTE_CURRENCIES:
        raise QuoteValidationError(
            "quote.invalid_currency",
            "currency 只允许 CNY/USD/EUR。",
            field="currency",
        )
    now = datetime.now()
    date_from = _record_date(data.get("date_from"), now=now) if compact_text(data.get("date_from")) else ""
    date_to = _record_date(data.get("date_to"), now=now) if compact_text(data.get("date_to")) else ""
    if date_from and date_to and date_from > date_to:
        raise QuoteValidationError(
            "quote.invalid_date_range",
            "date_from 不能晚于 date_to。",
            field="date_from",
        )
    return QuoteFilters(
        customer_name=compact_text(data.get("customer_name")),
        bld_no=compact_text(data.get("bld_no") or data.get("product_model")),
        date_from=date_from,
        date_to=date_to,
        currency=currency,
        quoted_by=compact_text(data.get("quoted_by")),
    )
