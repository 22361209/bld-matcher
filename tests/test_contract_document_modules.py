from __future__ import annotations

import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app import purchase_contract as facade
from app.modules.contracts import form_parser, purchase_pdf, sales_pdf


def purchase_form() -> MultiDict[str, str]:
    return MultiDict(
        [
            ("contract_no", "CG-BASELINE-001"),
            ("contract_date", "2026-07-11"),
            ("buyer_name", "玉环博莱德机械有限公司"),
            ("buyer_contact", "采购联系人"),
            ("buyer_phone", "0576-11111111"),
            ("supplier_name", "基准供应商"),
            ("supplier_contact", "供应联系人"),
            ("supplier_phone", "0576-22222222"),
            ("delivery_address", "浙江省玉环市基准仓库"),
            ("payment_terms", "月结 30 天"),
            ("quality_terms", "第一项质量要求\n第二项质量要求"),
            ("remark", "采购基准备注"),
            ("buyer_signature_address", "甲方地址"),
            ("supplier_signature_address", "乙方地址"),
            ("buyer_signature_phone", "0576-33333333"),
            ("supplier_signature_phone", "0576-44444444"),
            ("buyer_bank", "甲方银行"),
            ("supplier_bank", "乙方银行"),
            ("buyer_bank_account", "11112222"),
            ("supplier_bank_account", "33334444"),
            ("buyer_signature_date", "2026-07-12"),
            ("supplier_signature_date", "2026-07-13"),
            ("product_code[]", "K-BASE-001"),
            ("oe_no[]", "OE-BASE-001"),
            ("product_name[]", "基准控制臂"),
            ("models[]", "BASE MODEL"),
            ("quantity[]", "3"),
            ("unit_price[]", "88.80"),
            ("delivery_date[]", "2026-07-31"),
            ("item_note[]", "加急"),
        ]
    )


def sales_form() -> MultiDict[str, str]:
    return MultiDict(
        [
            ("contract_no", "XS-BASELINE-001"),
            ("contract_date", "2026-07-11"),
            ("seller_name", "玉环博莱德机械有限公司"),
            ("seller_contact", "销售联系人"),
            ("seller_phone", "0576-55555555"),
            ("customer_name", "基准销售客户"),
            ("customer_contact", "客户联系人"),
            ("customer_phone", "0576-66666666"),
            ("delivery_address", "客户基准仓库"),
            ("payment_terms", "月结 45 天"),
            ("quality_terms", "销售质量要求一\n销售质量要求二"),
            ("remark", "销售基准备注"),
            ("seller_credit_code", "SELLER-CREDIT"),
            ("customer_credit_code", "CUSTOMER-CREDIT"),
            ("seller_signature_address", "供方地址"),
            ("customer_signature_address", "需方地址"),
            ("seller_signature_phone", "0576-77777777"),
            ("customer_signature_phone", "0576-88888888"),
            ("seller_fax", "0576-70000001"),
            ("customer_fax", "0576-80000001"),
            ("seller_bank", "供方银行"),
            ("customer_bank", "需方银行"),
            ("seller_bank_account", "55556666"),
            ("customer_bank_account", "77778888"),
            ("seller_signature_date", "2026-07-14"),
            ("customer_signature_date", "2026-07-15"),
            ("product_code[]", "K-SALE-BASE-001"),
            ("customer_code[]", "CUST-BASE-001"),
            ("oe_no[]", "OE-SALE-BASE-001"),
            ("product_name[]", "销售基准控制臂"),
            ("models[]", "SALE MODEL"),
            ("quantity[]", "5"),
            ("unit_price[]", "99.50"),
            ("delivery_date[]", "2026-08-01"),
            ("item_note[]", "销售加急"),
        ]
    )


class ContractDocumentModuleTest(unittest.TestCase):
    def test_compatibility_facade_keeps_public_entrypoints(self) -> None:
        self.assertIs(facade.purchase_contract_from_form, form_parser.purchase_contract_from_form)
        self.assertIs(facade.sales_contract_from_form, form_parser.sales_contract_from_form)
        self.assertIs(facade.generate_purchase_contract_pdf, purchase_pdf.generate_purchase_contract_pdf)
        self.assertIs(facade.generate_sales_contract_pdf, sales_pdf.generate_sales_contract_pdf)

    def test_form_parsers_keep_totals_and_uppercase_amounts(self) -> None:
        purchase = form_parser.purchase_contract_from_form(purchase_form())
        sales = form_parser.sales_contract_from_form(sales_form())

        self.assertEqual(purchase["total_amount"], Decimal("266.40"))
        self.assertEqual(purchase["total_amount_upper"], "贰佰陆拾陆元肆角")
        self.assertEqual(sales["total_amount"], Decimal("497.50"))
        self.assertEqual(sales["total_amount_upper"], "肆佰玖拾柒元伍角")
        self.assertEqual(sales["items"][0]["customer_code"], "CUST-BASE-001")

    def test_purchase_and_sales_pdf_keep_two_page_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            outputs = (
                (
                    root / "purchase.pdf",
                    purchase_pdf.generate_purchase_contract_pdf,
                    form_parser.purchase_contract_from_form(purchase_form()),
                ),
                (
                    root / "sales.pdf",
                    sales_pdf.generate_sales_contract_pdf,
                    form_parser.sales_contract_from_form(sales_form()),
                ),
            )
            for path, generate, contract in outputs:
                generate(contract, path)
                payload = path.read_bytes()
                self.assertTrue(payload.startswith(b"%PDF-"))
                self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", payload)), 2)


if __name__ == "__main__":
    unittest.main()
