"""Compatibility facade for purchase and sales contract documents."""

from app.modules.contracts.document_defaults import (
    DEFAULT_BUYER_NAME,
    DEFAULT_DELIVERY_ADDRESS,
    DEFAULT_PAYMENT_TERMS,
    DEFAULT_PRICE_NOTE,
    DEFAULT_QUALITY_TERMS,
    DEFAULT_SALES_PAYMENT_TERMS,
    DEFAULT_SALES_PRICE_NOTE,
    DEFAULT_SALES_QUALITY_TERMS,
)
from app.modules.contracts.document_values import MONEY_QUANT
from app.modules.contracts.form_parser import (
    default_contract_no,
    default_sales_contract_no,
    purchase_contract_from_form,
    sales_contract_from_form,
)
from app.modules.contracts.pdf_support import (
    PDF_ASCII_FALLBACK_FONT,
    PDF_ASCII_FONT,
    PDF_ASCII_FONT_CANDIDATES,
    PDF_FONT,
    PROJECT_ROOT,
)
from app.modules.contracts.purchase_pdf import generate_purchase_contract_pdf
from app.modules.contracts.sales_pdf import generate_sales_contract_pdf

__all__ = [
    "DEFAULT_BUYER_NAME",
    "DEFAULT_DELIVERY_ADDRESS",
    "DEFAULT_PAYMENT_TERMS",
    "DEFAULT_PRICE_NOTE",
    "DEFAULT_QUALITY_TERMS",
    "DEFAULT_SALES_PAYMENT_TERMS",
    "DEFAULT_SALES_PRICE_NOTE",
    "DEFAULT_SALES_QUALITY_TERMS",
    "MONEY_QUANT",
    "PDF_ASCII_FALLBACK_FONT",
    "PDF_ASCII_FONT",
    "PDF_ASCII_FONT_CANDIDATES",
    "PDF_FONT",
    "PROJECT_ROOT",
    "default_contract_no",
    "default_sales_contract_no",
    "generate_purchase_contract_pdf",
    "generate_sales_contract_pdf",
    "purchase_contract_from_form",
    "sales_contract_from_form",
]
