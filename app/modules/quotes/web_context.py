from __future__ import annotations

from flask import url_for

from .domain import QuoteRecord


def quote_edit_payload(record: QuoteRecord, *, allow_delete: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": record.id,
        "version": record.version,
        "customer_name": record.customer_name,
        "bld_no": record.bld_no or record.product_model,
        "customer_product_code": record.customer_product_code,
        "tax_price": record.tax_price,
        "net_price": record.net_price,
        "currency": record.currency,
        "quote_date": record.quote_date,
        "remark": record.remark,
        "edit_url": url_for("quote_web.edit_quote", quote_id=record.id),
    }
    if allow_delete:
        payload["delete_url"] = url_for("quote_web.delete_quote", quote_id=record.id)
    return payload
