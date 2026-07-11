from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .document_defaults import (
    DEFAULT_BUYER_NAME,
    DEFAULT_DELIVERY_ADDRESS,
    DEFAULT_PAYMENT_TERMS,
    DEFAULT_PRICE_NOTE,
    DEFAULT_QUALITY_TERMS,
    DEFAULT_SALES_PAYMENT_TERMS,
    DEFAULT_SALES_PRICE_NOTE,
    DEFAULT_SALES_QUALITY_TERMS,
)
from .document_values import MONEY_QUANT, _parse_decimal, _rmb_upper, _text


def default_contract_no(username: str = "") -> str:
    user_part = _text(username) or "user"
    return f"CG-{datetime.now().strftime('%y%m%d-%H%M')}-{user_part}"


def default_sales_contract_no(username: str = "") -> str:
    user_part = _text(username) or "user"
    return f"BLZ-XS-{datetime.now().strftime('%Y%m%d-%H%M')}-{user_part}"


def purchase_contract_from_form(form: Any) -> dict[str, Any]:
    contract = {
        "contract_no": _text(form.get("contract_no")),
        "contract_date": _text(form.get("contract_date")) or date.today().isoformat(),
        "buyer_name": _text(form.get("buyer_name")) or DEFAULT_BUYER_NAME,
        "buyer_contact": _text(form.get("buyer_contact")),
        "buyer_phone": _text(form.get("buyer_phone")),
        "supplier_name": _text(form.get("supplier_name")),
        "supplier_contact": _text(form.get("supplier_contact")),
        "supplier_phone": _text(form.get("supplier_phone")),
        "buyer_signature_address": _text(form.get("buyer_signature_address")),
        "supplier_signature_address": _text(form.get("supplier_signature_address")),
        "buyer_signature_phone": _text(form.get("buyer_signature_phone")),
        "supplier_signature_phone": _text(form.get("supplier_signature_phone")),
        "buyer_bank": _text(form.get("buyer_bank")),
        "supplier_bank": _text(form.get("supplier_bank")),
        "buyer_bank_account": _text(form.get("buyer_bank_account")),
        "supplier_bank_account": _text(form.get("supplier_bank_account")),
        "buyer_signature_date": _text(form.get("buyer_signature_date")),
        "supplier_signature_date": _text(form.get("supplier_signature_date")),
        "delivery_address": _text(form.get("delivery_address")) or DEFAULT_DELIVERY_ADDRESS,
        "payment_terms": _text(form.get("payment_terms")) or DEFAULT_PAYMENT_TERMS,
        "price_note": _text(form.get("price_note")) or DEFAULT_PRICE_NOTE,
        "quality_terms": _text(form.get("quality_terms")) or DEFAULT_QUALITY_TERMS,
        "remark": _text(form.get("remark")),
        "items": [],
    }
    if not contract["contract_no"]:
        raise ValueError("合同编号不能为空。")
    if not contract["supplier_name"]:
        raise ValueError("供应商不能为空。")

    codes = form.getlist("product_code[]")
    oe_numbers = form.getlist("oe_no[]")
    names = form.getlist("product_name[]")
    models = form.getlist("models[]")
    quantities = form.getlist("quantity[]")
    prices = form.getlist("unit_price[]")
    deliveries = form.getlist("delivery_date[]")
    notes = form.getlist("item_note[]")
    row_count = max(
        len(codes),
        len(oe_numbers),
        len(names),
        len(models),
        len(quantities),
        len(prices),
        len(deliveries),
        len(notes),
    )

    total = Decimal("0")
    total_quantity = Decimal("0")
    for index in range(row_count):
        code = _text(codes[index] if index < len(codes) else "")
        oe_no = _text(oe_numbers[index] if index < len(oe_numbers) else "")
        name = _text(names[index] if index < len(names) else "")
        model_text = _text(models[index] if index < len(models) else "")
        quantity_text = _text(quantities[index] if index < len(quantities) else "")
        price_text = _text(prices[index] if index < len(prices) else "")
        delivery = _text(deliveries[index] if index < len(deliveries) else "")
        note = _text(notes[index] if index < len(notes) else "")
        if not any([code, oe_no, name, model_text, quantity_text, price_text, delivery, note]):
            continue
        row_label = f"第 {index + 1} 行"
        if not code:
            raise ValueError(f"{row_label}型号不能为空。")
        quantity = _parse_decimal(quantity_text, f"{row_label}数量", positive=True)
        unit_price = _parse_decimal(price_text, f"{row_label}单价")
        amount = (quantity * unit_price).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_quantity += quantity
        total += amount
        contract["items"].append(
            {
                "product_code": code,
                "oe_no": oe_no,
                "product_name": name,
                "models": model_text,
                "image_path": "",
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "delivery_date": delivery,
                "note": note,
            }
        )

    if not contract["items"]:
        raise ValueError("请至少填写一条采购明细。")

    contract["total_amount"] = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    contract["total_quantity"] = total_quantity
    contract["total_amount_upper"] = _rmb_upper(contract["total_amount"])
    return contract


def sales_contract_from_form(form: Any) -> dict[str, Any]:
    contract = {
        "contract_no": _text(form.get("contract_no")),
        "contract_date": _text(form.get("contract_date")) or date.today().isoformat(),
        "seller_name": _text(form.get("seller_name") or form.get("buyer_name")) or DEFAULT_BUYER_NAME,
        "seller_contact": _text(form.get("seller_contact") or form.get("buyer_contact")),
        "seller_phone": _text(form.get("seller_phone") or form.get("buyer_phone")),
        "customer_name": _text(form.get("customer_name") or form.get("supplier_name")),
        "customer_contact": _text(form.get("customer_contact") or form.get("supplier_contact")),
        "customer_phone": _text(form.get("customer_phone") or form.get("supplier_phone")),
        "seller_credit_code": _text(form.get("seller_credit_code")),
        "customer_credit_code": _text(form.get("customer_credit_code")),
        "seller_signature_address": _text(form.get("seller_signature_address") or form.get("buyer_signature_address")),
        "customer_signature_address": _text(
            form.get("customer_signature_address") or form.get("supplier_signature_address")
        ),
        "seller_signature_phone": _text(form.get("seller_signature_phone") or form.get("buyer_signature_phone")),
        "customer_signature_phone": _text(form.get("customer_signature_phone") or form.get("supplier_signature_phone")),
        "seller_fax": _text(form.get("seller_fax")),
        "customer_fax": _text(form.get("customer_fax")),
        "seller_bank": _text(form.get("seller_bank") or form.get("buyer_bank")),
        "customer_bank": _text(form.get("customer_bank") or form.get("supplier_bank")),
        "seller_bank_account": _text(form.get("seller_bank_account") or form.get("buyer_bank_account")),
        "customer_bank_account": _text(form.get("customer_bank_account") or form.get("supplier_bank_account")),
        "seller_signature_date": _text(form.get("seller_signature_date") or form.get("buyer_signature_date")),
        "customer_signature_date": _text(form.get("customer_signature_date") or form.get("supplier_signature_date")),
        "delivery_address": _text(form.get("delivery_address")),
        "payment_terms": _text(form.get("payment_terms")) or DEFAULT_SALES_PAYMENT_TERMS,
        "price_note": _text(form.get("price_note")) or DEFAULT_SALES_PRICE_NOTE,
        "quality_terms": _text(form.get("quality_terms")) or DEFAULT_SALES_QUALITY_TERMS,
        "remark": _text(form.get("remark")),
        "items": [],
    }
    if not contract["contract_no"]:
        raise ValueError("合同编号不能为空。")
    if not contract["customer_name"]:
        raise ValueError("需方不能为空。")

    codes = form.getlist("product_code[]")
    customer_codes = form.getlist("customer_code[]")
    oe_numbers = form.getlist("oe_no[]")
    names = form.getlist("product_name[]")
    models = form.getlist("models[]")
    quantities = form.getlist("quantity[]")
    prices = form.getlist("unit_price[]")
    deliveries = form.getlist("delivery_date[]")
    notes = form.getlist("item_note[]")
    row_count = max(
        len(codes),
        len(customer_codes),
        len(oe_numbers),
        len(names),
        len(models),
        len(quantities),
        len(prices),
        len(deliveries),
        len(notes),
    )

    total = Decimal("0")
    total_quantity = Decimal("0")
    for index in range(row_count):
        code = _text(codes[index] if index < len(codes) else "")
        customer_code = _text(customer_codes[index] if index < len(customer_codes) else "")
        oe_no = _text(oe_numbers[index] if index < len(oe_numbers) else "")
        name = _text(names[index] if index < len(names) else "")
        model_text = _text(models[index] if index < len(models) else "")
        quantity_text = _text(quantities[index] if index < len(quantities) else "")
        price_text = _text(prices[index] if index < len(prices) else "")
        delivery = _text(deliveries[index] if index < len(deliveries) else "")
        note = _text(notes[index] if index < len(notes) else "")
        if not any([code, customer_code, oe_no, name, model_text, quantity_text, price_text, delivery, note]):
            continue
        row_label = f"第 {index + 1} 行"
        if not code:
            raise ValueError(f"{row_label}型号不能为空。")
        quantity = _parse_decimal(quantity_text, f"{row_label}数量", positive=True)
        unit_price = _parse_decimal(price_text, f"{row_label}单价")
        amount = (quantity * unit_price).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_quantity += quantity
        total += amount
        contract["items"].append(
            {
                "product_code": code,
                "customer_code": customer_code,
                "oe_no": oe_no,
                "product_name": name,
                "models": model_text,
                "image_path": "",
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "delivery_date": delivery,
                "note": note,
            }
        )

    if not contract["items"]:
        raise ValueError("请至少填写一条销售明细。")

    contract["total_amount"] = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    contract["total_quantity"] = total_quantity
    contract["total_amount_upper"] = _rmb_upper(contract["total_amount"])
    return contract
