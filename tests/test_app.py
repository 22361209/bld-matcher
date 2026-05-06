from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import unittest
import zipfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_web_module():
    spec = spec_from_file_location("bld_matcher_test_web", PROJECT_ROOT / "app.py")
    module = module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["bld_matcher_test_web"] = module
    spec.loader.exec_module(module)
    return module


class WebAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.root = root
        os.environ["SECRET_KEY"] = "test-secret"
        os.environ["MAX_UPLOAD_MB"] = "20"
        os.environ["BLD_DATA_DIR"] = str(root / "data")
        os.environ["BLD_UPLOAD_DIR"] = str(root / "uploads")
        os.environ["BLD_OUTPUT_DIR"] = str(root / "outputs")
        os.environ["DEFAULT_ADMIN_PASSWORD"] = "test-admin-pw"
        cls.web = load_web_module()
        cls.web.app.config["TESTING"] = True
        cls.client = cls.web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def login(self):
        return self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "next": "/"},
            follow_redirects=False,
        )

    def test_login_and_homepage(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)

        response = self.login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("BLD", html)
        self.assertLess(html.index('class="messages'), html.index('class="inquiry-landing"'))
        self.assertIn('class="embedded-submit" type="submit">开始匹配', html)
        self.assertIn('class="embedded-input-control"', html)
        self.assertIn('class="embedded-submit" type="submit">搜索', html)
        nav_order = ["询价处理", "价格维护", "合同管理", "产品目录", "生产料单"]
        nav_positions = [html.index(label) for label in nav_order]
        self.assertEqual(nav_positions, sorted(nav_positions))

    def test_login_next_rejects_external_url(self):
        response = self.client.post(
            "/login",
            data={"username": "007", "password": "test-admin-pw", "next": "https://example.com/phish"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("example.com", response.headers["Location"])

    def test_download_does_not_send_directories(self):
        self.login()
        output_dir = self.root / "outputs" / "u1-007"
        output_dir.mkdir(parents=True, exist_ok=True)
        response = self.client.get("/download/u1-007", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

    def test_core_admin_pages_load(self):
        self.login()
        for path in ["/customer-prices", "/contracts", "/contracts/sales", "/products", "/materials", "/purchase-contracts", "/users", "/logs", "/system-updates"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_purchase_contract_can_generate_pdf(self):
        from PIL import Image
        from app.database import upsert_product

        image_dir = self.root / "data" / "product_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (96, 64), "white").save(image_dir / "K-OUT-001.png")
        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-OUT-001",
                    "oe_no_1": "OE-CATALOG-001",
                    "item": "目录外购支架",
                    "models": "目录车型",
                    "price_cny": "45.5",
                    "image_path": "data_product_images/K-OUT-001.png",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/contracts")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("合同管理", html)
        self.assertIn("采购合同", html)
        self.assertIn("销售合同", html)
        self.assertNotIn("销售合同模板后续接入", html)
        self.assertIn("玉环博莱德机械有限公司", html)
        self.assertIn("浙江省玉环市金汇路11号", html)
        self.assertIn("月结 30 天", html)
        self.assertIn('name="product_code[]"', html)
        self.assertIn('name="oe_no[]"', html)
        self.assertIn('name="models[]"', html)
        self.assertIn('data-add-purchase-row', html)
        self.assertIn('data-supplier-sign-name', html)
        self.assertIn('data-purchase-confirm-modal', html)
        self.assertIn("确认生成 PDF", html)
        self.assertNotIn("统一社会信用代码", html)
        self.assertIn('name="buyer_signature_address"', html)
        self.assertIn('name="supplier_signature_address"', html)
        self.assertIn('name="buyer_bank"', html)
        self.assertIn('name="supplier_bank_account"', html)
        self.assertIn('name="buyer_signature_date"', html)
        self.assertIn("supplier-detail-line", html)

        lookup = self.client.get("/purchase-contracts/product-lookup", query_string={"bld": "K-OUT-001"})
        self.assertEqual(lookup.status_code, 200)
        payload = lookup.get_json()
        self.assertTrue(payload["found"])
        self.assertEqual(payload["oe_no"], "OE-CATALOG-001")
        self.assertEqual(payload["product_name"], "目录外购支架")
        self.assertEqual(payload["models"], "目录车型")
        self.assertEqual(payload["price_cny"], 45.5)
        self.assertIn("product-image-thumbs", payload["thumb_url"])

        response = self.client.post(
            "/purchase-contracts/generate",
            data={
                "contract_no": "CG-TEST-001",
                "contract_date": "2026-05-05",
                "buyer_name": "玉环博莱德机械有限公司",
                "buyer_contact": "李四",
                "buyer_phone": "13900000000",
                "supplier_name": "外购供应商",
                "supplier_contact": "张三",
                "supplier_phone": "13800000000",
                "buyer_signature_address": "甲方签章地址",
                "supplier_signature_address": "乙方签章地址",
                "buyer_signature_phone": "0576-11111111",
                "supplier_signature_phone": "0576-22222222",
                "buyer_bank": "甲方开户行",
                "supplier_bank": "乙方开户行",
                "buyer_bank_account": "11112222",
                "supplier_bank_account": "33334444",
                "buyer_signature_date": "2026-05-06",
                "supplier_signature_date": "2026-05-07",
                "delivery_address": "浙江省玉环市",
                "price_note": "以上价格为含税价（增值税税率13%），含包装费及运费，送达甲方指定地点。",
                "payment_terms": "月结",
                "quality_terms": "按图纸执行",
                "product_code[]": ["K-OUT-001", ""],
                "oe_no[]": ["手填OE", ""],
                "product_name[]": ["手填名称", ""],
                "models[]": ["手填车型", ""],
                "quantity[]": ["10", ""],
                "unit_price[]": ["25.5", ""],
                "delivery_date[]": ["2026-05-20", ""],
                "item_note[]": ["加急", ""],
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("CG-TEST-001", response.headers["Content-Disposition"])
        self.assertTrue(response.get_data().startswith(b"%PDF-"))
        response.close()
        files = list((self.root / "outputs").glob("u*-007/采购合同/外购供应商/CG-TEST-001外购供应商.pdf"))
        self.assertEqual(len(files), 1)

    def test_sales_contract_can_generate_pdf_with_customer_code(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-SALE-001",
                    "oe_no_1": "OE-SALE-001",
                    "item": "销售控制臂",
                    "models": "销售车型",
                    "price_cny": "88.8",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/contracts/sales")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("产 品 销 售 合 同", html)
        self.assertIn("供方（甲方）", html)
        self.assertIn("需方（乙方）", html)
        self.assertIn("客户编码", html)
        self.assertIn('name="customer_code[]"', html)
        self.assertIn('action="/sales-contracts/generate"', html)
        self.assertIn("甲方按行业通用标准及乙方要求进行包装", html)
        self.assertIn("增值税专用发票（税率 13%）的开具时间由双方另行约定", html)

        response = self.client.post(
            "/sales-contracts/generate",
            data={
                "contract_no": "XS-TEST-001",
                "contract_date": "2026-05-06",
                "buyer_name": "玉环博莱德机械有限公司",
                "buyer_contact": "李四",
                "buyer_phone": "13900000000",
                "supplier_name": "销售客户",
                "supplier_contact": "王五",
                "supplier_phone": "13700000000",
                "delivery_address": "客户仓库",
                "price_note": "以上价格为含税价。",
                "payment_terms": "月结 30 天",
                "quality_terms": "按封样执行",
                "product_code[]": ["K-SALE-001"],
                "customer_code[]": ["CUST-001"],
                "oe_no[]": ["手填销售OE"],
                "product_name[]": ["手填销售名称"],
                "models[]": ["手填销售车型"],
                "quantity[]": ["3"],
                "unit_price[]": ["88.8"],
                "delivery_date[]": ["2026-05-25"],
                "item_note[]": ["销售备注"],
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("XS-TEST-001", response.headers["Content-Disposition"])
        self.assertTrue(response.get_data().startswith(b"%PDF-"))
        response.close()
        files = list((self.root / "outputs").glob("u*-007/销售合同/销售客户/XS-TEST-001销售客户.pdf"))
        self.assertEqual(len(files), 1)

    def test_purchase_contract_signature_fields_are_optional(self):
        from app.purchase_contract import purchase_contract_from_form

        class FormData(dict):
            def getlist(self, key):
                value = self.get(key, [])
                return value if isinstance(value, list) else [value]

        contract = purchase_contract_from_form(
            FormData(
                {
                    "contract_no": "CG-OPTIONAL-SIGN",
                    "contract_date": "2026-05-06",
                    "buyer_name": "甲方公司",
                    "supplier_name": "乙方公司",
                    "product_code[]": ["K-OPTIONAL-001"],
                    "quantity[]": ["1"],
                    "unit_price[]": ["2.50"],
                }
            )
        )

        self.assertEqual(contract["buyer_signature_address"], "")
        self.assertEqual(contract["supplier_signature_phone"], "")
        self.assertEqual(contract["buyer_bank"], "")
        self.assertEqual(contract["supplier_bank_account"], "")
        self.assertEqual(contract["buyer_signature_date"], "")

    def test_purchase_contracts_are_admin_only(self):
        from app.database import save_user

        with self.web.connect(self.web.DB_PATH) as conn:
            save_user(
                conn,
                {
                    "username": "editor-contracts",
                    "display_name": "Editor Contracts",
                    "password": "editor-pw",
                    "role": "editor",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        admin_page = self.client.get("/").get_data(as_text=True)
        self.assertIn("合同管理", admin_page)
        self.client.post("/logout")

        login = self.client.post(
            "/login",
            data={"username": "editor-contracts", "password": "editor-pw", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        editor_page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("合同管理", editor_page)
        for path in ["/contracts", "/contracts/sales", "/purchase-contracts"]:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/"))

        response = self.client.get("/purchase-contracts/product-lookup", query_string={"bld": "K-OUT-001"}, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))

        for path in ["/purchase-contracts/generate", "/sales-contracts/generate"]:
            with self.subTest(path=path):
                response = self.client.post(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/"))
        self.client.post("/logout")

    def test_customer_price_records_can_filter_and_import(self):
        from openpyxl import Workbook

        self.login()
        response = self.client.get("/customer-prices")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("价格维护", html)
        self.assertIn('action="/customer-prices#customer-price-results"', html)
        self.assertIn("客户概览", html)
        self.assertIn('name="customer_q"', html)
        self.assertIn('name="bld_no"', html)
        self.assertIn('name="source_code"', html)
        self.assertNotIn('name="date_from"', html)
        self.assertNotIn('name="date_to"', html)
        self.assertNotIn("客户号码 / OE", html)
        self.assertNotIn("<th>数量</th>", html)

        response = self.client.post(
            "/customer-prices/save",
            data={
                "record_type": "quote",
                "customer_name": "ACME",
                "record_date": "2026-05-05",
                "source_code": "ACME-001",
                "bld_no": "K-PRICE-001",
                "item": "Control Arm",
                "price_cny": "88.5",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.post(
            "/customer-prices/save",
            data={
                "record_type": "quote",
                "customer_name": "OMEGA",
                "record_date": "2026-05-05",
                "source_code": "OMG-001",
                "bld_no": "K-PRICE-001",
                "item": "Control Arm",
                "price_cny": "92",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get("/customer-prices")
        html = response.get_data(as_text=True)
        self.assertIn("客户概览", html)
        self.assertIn("ACME", html)
        self.assertIn('href="/customer-prices?customer=ACME#customer-price-results"', html)
        self.assertIn("查看明细", html)

        response = self.client.get("/customer-prices", query_string={"customer_q": "ACM"})
        html = response.get_data(as_text=True)
        self.assertIn("客户概览", html)
        self.assertIn("ACME", html)
        self.assertNotIn("价格明细", html)

        response = self.client.get("/customer-prices", query_string={"customer": "ACME"})
        html = response.get_data(as_text=True)
        self.assertIn("价格明细", html)
        self.assertIn("返回客户概览", html)

        response = self.client.get("/customer-prices", query_string={"bld_no": "K-PRICE"})
        html = response.get_data(as_text=True)
        self.assertIn("价格明细", html)
        self.assertIn("型号价格对比", html)
        self.assertIn("<th>客户号码</th>", html)
        self.assertIn("<th>OE 号</th>", html)
        self.assertIn("K-PRICE-001", html)
        self.assertIn("¥88.50", html)
        self.assertIn("OMEGA", html)
        self.assertIn("¥92.00", html)
        self.assertIn("ACME-001", html)
        self.assertIn('id="customer-price-delete-modal"', html)
        self.assertIn("data-open-customer-price-delete", html)

        response = self.client.get("/customer-prices", query_string={"source_code": "ACME-001"})
        html = response.get_data(as_text=True)
        self.assertIn("价格明细", html)
        self.assertIn("ACME-001", html)

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["客户", "日期", "BLD NO.", "OE号", "含税单价"])
        sheet.append(["PIKA", "2026-05-05", "K-ORDER-001", "54500-2D000", 120])
        payload = io.BytesIO()
        workbook.save(payload)
        workbook.close()
        payload.seek(0)

        response = self.client.post(
            "/customer-prices/import",
            data={
                "record_type": "order",
                "price_file": (payload, "orders.xlsx"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get("/customer-prices", query_string={"record_type": "order", "bld_no": "K-ORDER"})
        html = response.get_data(as_text=True)
        self.assertIn("PIKA", html)
        self.assertIn("K-ORDER-001", html)
        self.assertIn("成交", html)

    def test_customer_prices_are_admin_only(self):
        from app.database import save_user

        with self.web.connect(self.web.DB_PATH) as conn:
            save_user(
                conn,
                {
                    "username": "editor-prices",
                    "display_name": "Editor Prices",
                    "password": "editor-pw",
                    "role": "editor",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        admin_page = self.client.get("/").get_data(as_text=True)
        self.assertIn("价格维护", admin_page)
        self.client.post("/logout")

        login = self.client.post(
            "/login",
            data={"username": "editor-prices", "password": "editor-pw", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        editor_page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("价格维护", editor_page)
        response = self.client.get("/customer-prices", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        self.client.post("/logout")

    def test_products_search_uses_results_anchor(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-FILTER-HYUNDAI",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "FILTER-001",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )
            upsert_product(
                conn,
                {
                    "bld_no": "K-FILTER-HONDA",
                    "series": "HONDA",
                    "item": "CONTROL ARM",
                    "oe_no_1": "FILTER-002",
                    "models": "Civic",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/products")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="products-results"', html)
        self.assertIn('action="/products#products-results"', html)
        self.assertIn("按 BLD / 品牌 / 车型搜索", html)
        self.assertIn('<button class="linear-button primary" type="submit">搜索</button>', html)
        self.assertIn('class="embedded-submit" type="submit">上传预览', html)
        self.assertIn('class="embedded-submit" type="submit">确认导入', html)

        for query in ["HYUNDAI", "Sportage"]:
            with self.subTest(query=query):
                response = self.client.get("/products", query_string={"bld": query})
                html = response.get_data(as_text=True)
                self.assertIn("K-FILTER-HYUNDAI", html)
                self.assertNotIn("K-FILTER-HONDA", html)

    def test_products_are_paginated(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            for index in range(121):
                upsert_product(
                    conn,
                    {
                        "bld_no": f"K-PAGE-{index:03d}",
                        "series": "PAGED",
                        "item": "PAGED PART",
                        "oe_no_1": f"PAGE-{index:03d}",
                        "models": "Batch Tester",
                        "active": "1",
                    },
                    actor="tester",
                )

        self.login()
        response = self.client.get("/products", query_string={"bld": "K-PAGE"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("第 1-50 条 / 共 121 条", html)
        self.assertIn("第 1 / 3 页，每页 50 条", html)
        self.assertEqual(html.count('aria-label="产品分页"'), 1)
        self.assertIn("K-PAGE-000", html)
        self.assertIn("K-PAGE-049", html)
        self.assertNotIn("K-PAGE-050", html)
        self.assertNotIn("/products/rows", html)

        third_page = self.client.get("/products", query_string={"bld": "K-PAGE", "page": "3"})
        third_html = third_page.get_data(as_text=True)
        self.assertEqual(third_page.status_code, 200)
        self.assertNotIn("第 101-121 条 / 共 121 条", third_html)
        self.assertIn("第 3 / 3 页，每页 50 条", third_html)
        self.assertIn("K-PAGE-100", third_html)
        self.assertIn("K-PAGE-120", third_html)
        self.assertNotIn("K-PAGE-099", third_html)

    def test_product_drawing_upload_preview_and_batch_entry(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-DRAW-001",
                    "series": "TEST",
                    "item": "DRAWING PART",
                    "oe_no_1": "DRAW-001",
                    "models": "Tester",
                    "active": "1",
                },
                actor="tester",
            )
            product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-001",)).fetchone()

        self.login()
        response = self.client.get("/products", query_string={"bld": "K-DRAW-001"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PDF图纸", html)
        self.assertIn("批量上传图纸", html)
        self.assertNotIn('name="drawing"', html)
        self.assertNotIn(f'href="/products/{product["id"]}/drawing"', html)

        edit = self.client.get(f"/products/{product['id']}/edit")
        edit_html = edit.get_data(as_text=True)
        self.assertEqual(edit.status_code, 200)
        for slot in range(1, 6):
            self.assertIn(f'name="product_image_{slot}"', edit_html)
        self.assertIn("file-picker-clear", edit_html)
        self.assertIn("/static/app.js", edit_html)
        self.assertIn('name="drawing"', edit_html)

        upload = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-001",
                "series": "TEST",
                "item": "DRAWING PART",
                "oe_no_1": "DRAW-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "active": "1",
                "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\nproduct image 1"), "K-DRAW-001.png"),
                "product_image_2": (io.BytesIO(b"\x89PNG\r\n\x1a\nproduct image 2"), "K-DRAW-001-2.png"),
                "drawing": (io.BytesIO(b"%PDF-1.4\nfirst drawing\n%%EOF"), "K-DRAW-001.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(upload.status_code, 302)

        with self.web.connect(self.web.DB_PATH) as conn:
            updated = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-001",)).fetchone()
        drawing_path = self.root / "data" / updated["drawing_path"]
        image_path = self.root / "data" / "product_images" / "K-DRAW-001.png"
        image_path_2 = self.root / "data" / "product_images" / "K-DRAW-001-2.png"
        self.assertTrue(drawing_path.exists())
        self.assertTrue(image_path.exists())
        self.assertTrue(image_path_2.exists())
        self.assertEqual(updated["drawing_original_name"], "K-DRAW-001.pdf")
        self.assertEqual(updated["image_path"], "data_product_images/K-DRAW-001.png")
        self.assertEqual(updated["image_path_2"], "data_product_images/K-DRAW-001-2.png")

        response = self.client.get("/products", query_string={"bld": "K-DRAW-001"})
        html = response.get_data(as_text=True)
        self.assertIn(f'href="/products/{product["id"]}/drawing"', html)
        self.assertNotIn("替换图纸", html)
        self.assertIn("/product-image-thumbs/K-DRAW-001.png", html)
        self.assertIn("/product-images/K-DRAW-001.png", html)
        self.assertIn("/product-images/K-DRAW-001-2.png", html)

        image = self.client.get("/product-images/K-DRAW-001.png")
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.get_data().startswith(b"\x89PNG"))
        image.close()

        preview = self.client.get(f"/products/{product['id']}/drawing")
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.get_data().startswith(b"%PDF-1.4"))
        preview.close()

        replace = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-001",
                "series": "TEST",
                "item": "DRAWING PART",
                "oe_no_1": "DRAW-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "active": "1",
                "drawing": (io.BytesIO(b"%PDF-1.4\nsecond drawing\n%%EOF"), "K-DRAW-001-v2.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(replace.status_code, 302)
        archive_dir = self.root / "data" / "drawings" / "archive" / "K-DRAW-001"
        self.assertTrue(list(archive_dir.glob("*.pdf")))

        batch = self.client.get("/products/drawings/batch")
        self.assertEqual(batch.status_code, 200)
        self.assertIn("暂未开放", batch.get_data(as_text=True))

    def test_product_image_table_uses_generated_thumbnail(self):
        from PIL import Image

        from app.database import upsert_product

        image_dir = self.root / "data" / "product_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path = image_dir / "K-THUMB-001.png"
        Image.new("RGB", (640, 360), "white").save(image_path)

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-THUMB-001",
                    "series": "TEST",
                    "item": "THUMB PART",
                    "oe_no_1": "THUMB-001",
                    "models": "Tester",
                    "image_path": "data_product_images/K-THUMB-001.png",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/products", query_string={"bld": "K-THUMB-001"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("/product-image-thumbs/K-THUMB-001.png", html)
        self.assertIn("/product-images/K-THUMB-001.png", html)

        thumb = self.client.get("/product-image-thumbs/K-THUMB-001.png")
        self.assertEqual(thumb.status_code, 200)
        with Image.open(io.BytesIO(thumb.get_data())) as generated:
            self.assertLessEqual(generated.width, 160)
            self.assertLessEqual(generated.height, 120)
        thumb.close()
        self.assertTrue((image_dir / "thumbs" / "K-THUMB-001.png").exists())

    def test_product_edit_can_delete_product(self):
        from app.database import save_alias, upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-DELETE-001",
                    "series": "TEST",
                    "item": "DELETE TARGET",
                    "oe_no_1": "DELETE-001",
                    "models": "Tester",
                    "active": "1",
                },
                actor="tester",
            )
            save_alias(conn, "DELETE-ALIAS-001", "K-DELETE-001", actor="tester")
            product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DELETE-001",)).fetchone()

        self.login()
        edit = self.client.get(f"/products/{product['id']}/edit")
        edit_html = edit.get_data(as_text=True)
        self.assertEqual(edit.status_code, 200)
        self.assertIn("删除产品", edit_html)
        self.assertIn(f'formaction="/products/{product["id"]}/delete"', edit_html)
        self.assertIn("confirm('确认删除 K-DELETE-001", edit_html)

        delete = self.client.post(f"/products/{product['id']}/delete", follow_redirects=False)
        self.assertEqual(delete.status_code, 302)
        self.assertTrue(delete.headers["Location"].endswith("/products"))

        with self.web.connect(self.web.DB_PATH) as conn:
            deleted = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DELETE-001",)).fetchone()
            alias = conn.execute("SELECT * FROM aliases WHERE source_code = ?", ("DELETEALIAS001",)).fetchone()
            log = conn.execute(
                "SELECT * FROM audit_logs WHERE action = ? AND target_key = ? ORDER BY id DESC LIMIT 1",
                ("删除产品", "K-DELETE-001"),
            ).fetchone()

        self.assertIsNone(deleted)
        self.assertEqual(alias["active"], 0)
        self.assertIsNotNone(log)
        self.assertIn("DELETE TARGET", log["detail"])

    def test_system_updates_page_reads_handoff_notes(self):
        self.login()
        response = self.client.get("/system-updates")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("系统更新", html)
        self.assertIn("当前最近重要变更", html)
        self.assertIn("项目交接说明.md", html)
        self.assertIn("ac3aa1a", html)
        self.assertIn("新增系统更新页面", html)

    def test_new_material_item_uses_modal(self):
        self.login()
        response = self.client.get("/materials")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-open-material-modal", html)
        self.assertIn('id="material-modal"', html)
        self.assertIn('action="/materials/items/save"', html)
        self.assertIn('id="materials-results"', html)
        self.assertIn('action="/materials#materials-results"', html)
        self.assertIn("data-enter-navigation", html)
        self.assertIn('name="spec_text"', html)
        self.assertIn('<button class="linear-button" type="submit">搜索</button>', html)
        self.assertIn('class="embedded-submit" type="submit">生成并下载', html)
        self.assertIn('class="embedded-submit" type="submit">确认导入', html)
        self.assertIn("母件编码", html)
        self.assertIn("零件编码", html)
        self.assertRegex(html, r'<input name="code"[^>]*required')
        self.assertRegex(html, r'<input name="part"[^>]*required')
        self.assertIn("<th>母件编码</th>", html)
        self.assertIn("<th>零件编码</th>", html)
        self.assertIn("<th>单件重量kg</th>", html)
        self.assertNotIn("<th>型号</th>", html)
        self.assertNotIn("<th>编码</th>", html)
        self.assertNotIn('name="thickness"', html)
        self.assertNotIn('name="width"', html)
        self.assertNotIn('name="length"', html)
        self.assertNotIn('href="/materials/items/new"', html)
        self.assertLess(html.index('name="part"'), html.index('name="pieces"'))
        self.assertLess(html.index('name="spec_text"'), html.index('name="category"'))
        self.assertLess(html.index('name="category"'), html.index('name="car"'))

    def test_material_item_save_calculates_spec_text(self):
        self.login()
        examples = [
            ("T-SPEC-WEB-SPACE", "2.5 357 1260", "2.5×357×1260"),
            ("T-SPEC-WEB-STAR", "2.5*357*1260", "2.5×357×1260"),
            ("T-SPEC-WEB-DASH", "2.5-357-1260", "2.5×357×1260"),
            ("T-SPEC-WEB-SLASH", "2.5/357/1260", "2.5×357×1260"),
        ]
        for model, spec_text, expected in examples:
            with self.subTest(spec_text=spec_text):
                response = self.client.post(
                    "/materials/items/save",
                    data={
                        "model": model,
                        "code": "KA-TEST",
                        "category": "测试类别",
                        "car": "测试车型",
                        "part": "测试零件",
                        "pieces": "2",
                        "spec_text": spec_text,
                        "active": "1",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 302)

        with self.web.connect(self.web.DB_PATH) as conn:
            rows = conn.execute(
                "SELECT model, spec_text, thickness, width, length FROM material_items WHERE model LIKE 'T-SPEC-WEB-%'"
            ).fetchall()
        saved = {row["model"]: row for row in rows}
        for model, _, expected in examples:
            self.assertIn(model, saved)
            self.assertEqual(saved[model]["spec_text"], expected)
            self.assertEqual(saved[model]["thickness"], 2.5)
            self.assertEqual(saved[model]["width"], 357)
            self.assertEqual(saved[model]["length"], 1260)

        response = self.client.get("/materials?q=T-SPEC-WEB-SPACE")
        html = response.get_data(as_text=True)
        self.assertIn("单件重量kg", html)
        self.assertIn("4.41", html)
        for query in ["357", "2.5 357", "357/1260", "2.5-1260", "2.5*357*1260"]:
            with self.subTest(query=query):
                response = self.client.get("/materials", query_string={"q": query})
                html = response.get_data(as_text=True)
                self.assertIn("T-SPEC-WEB-SPACE", html)
        response = self.client.get("/materials", query_string={"q": "2.5 999"})
        html = response.get_data(as_text=True)
        self.assertNotIn("T-SPEC-WEB-SPACE", html)

    def test_material_item_requires_code_and_part(self):
        self.login()
        for field, data in [
            (
                "code",
                {
                    "model": "T-SPEC-REQUIRED-CODE",
                    "part": "测试零件",
                    "pieces": "2",
                    "spec_text": "2.5 357 1260",
                    "active": "1",
                },
            ),
            (
                "part",
                {
                    "model": "T-SPEC-REQUIRED-PART",
                    "code": "KA-TEST",
                    "pieces": "2",
                    "spec_text": "2.5 357 1260",
                    "active": "1",
                },
            ),
        ]:
            with self.subTest(field=field):
                response = self.client.post("/materials/items/save", data=data, follow_redirects=False)
                self.assertEqual(response.status_code, 302)

        with self.web.connect(self.web.DB_PATH) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM material_items WHERE model IN (?, ?)",
                ("T-SPEC-REQUIRED-CODE", "T-SPEC-REQUIRED-PART"),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_material_import_calculates_spec_text_from_dimensions(self):
        from app.database import import_materials_from_excel
        from openpyxl import Workbook

        path = self.root / "stale-material-spec.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "材料数据"
        sheet.append(["型号", "型号", "类别", "车型", "零件名称", "规格尺寸", "下料只数", "单重", "规格1", "规格2", "规格3"])
        sheet.append(["T-SPEC-IMPORT", "KA-IMPORT", "测试类别", "测试车型", "测试零件", "旧规格", 3, "", 4, 92.5, 1260])
        workbook.save(path)
        workbook.close()

        with self.web.connect(self.web.DB_PATH) as conn:
            imported = import_materials_from_excel(conn, path, replace=False, actor="tester")
            row = conn.execute("SELECT spec_text FROM material_items WHERE model = ?", ("T-SPEC-IMPORT",)).fetchone()

        self.assertEqual(imported, 1)
        self.assertIsNotNone(row)
        self.assertEqual(row["spec_text"], "4.0×92.5×1260")

    def test_material_source_sync_rewrites_spec_text_column(self):
        from app.material_sheet import sync_material_specs_from_dimensions
        from openpyxl import Workbook, load_workbook

        path = self.root / "sync-material-spec.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "材料数据"
        sheet.append(["型号", "型号", "类别", "车型", "零件名称", "规格尺寸", "下料只数", "单重", "规格1", "规格2", "规格3"])
        sheet.append(["T-SPEC-SOURCE", "KA-SOURCE", "测试类别", "测试车型", "测试零件", "旧规格", 3, "", 2.5, 312, 1260])
        workbook.save(path)
        workbook.close()

        changed = sync_material_specs_from_dimensions(path)
        synced = load_workbook(path, read_only=True, data_only=True)
        try:
            self.assertEqual(changed, 1)
            self.assertEqual(synced["材料数据"].cell(2, 6).value, "2.5×312×1260")
        finally:
            synced.close()

    def test_upload_limit_is_20mb(self):
        self.assertEqual(self.web.app.config["MAX_CONTENT_LENGTH"], 20 * 1024 * 1024)

    def test_oversized_upload_redirects(self):
        self.login()
        original_limit = self.web.app.config["MAX_CONTENT_LENGTH"]
        self.web.app.config["MAX_CONTENT_LENGTH"] = 10
        try:
            big_file = io.BytesIO(b"x" * 11)
            response = self.client.post(
                "/catalog",
                data={"catalog": (big_file, "big.xlsx")},
                content_type="multipart/form-data",
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/products"))
            response.close()
        finally:
            self.web.app.config["MAX_CONTENT_LENGTH"] = original_limit

    def test_migrations_are_recorded(self):
        with self.web.connect(self.web.DB_PATH) as conn:
            rows = conn.execute("SELECT id FROM schema_migrations ORDER BY id").fetchall()
        self.assertEqual(
            [row["id"] for row in rows],
            ["001_audit_log_actor", "002_product_price_and_image", "003_product_drawings", "004_product_image_slots"],
        )

    def test_generated_files_are_scoped_to_user(self):
        self.login()
        user_files = set((self.root / "outputs").glob("u*-007/catalog-export-bld-007-*.xlsx"))
        response = self.client.post("/products/export", data={"status": "active", "export_format": "bld"})
        self.assertEqual(response.status_code, 200)
        response.close()

        files = set((self.root / "outputs").glob("u*-007/catalog-export-bld-007-*.xlsx")) - user_files
        self.assertEqual(len(files), 1)
        self.assertFalse(list((self.root / "outputs").glob("catalog-export-bld-007-*.xlsx")))

    def test_catalog_export_is_admin_only(self):
        from app.database import save_user

        with self.web.connect(self.web.DB_PATH) as conn:
            save_user(
                conn,
                {
                    "username": "editor-export",
                    "display_name": "Editor Export",
                    "password": "editor-pw",
                    "role": "editor",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        admin_page = self.client.get("/products").get_data(as_text=True)
        self.assertIn("导出目录", admin_page)

        self.client.post("/logout")
        login = self.client.post(
            "/login",
            data={"username": "editor-export", "password": "editor-pw", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        editor_page = self.client.get("/products").get_data(as_text=True)
        self.assertNotIn("导出目录", editor_page)
        self.assertNotIn('action="/products/export"', editor_page)

        response = self.client.post("/products/export", data={"status": "active", "export_format": "bld"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        self.assertFalse(list((self.root / "outputs").glob("**/catalog-export-bld-editor-export-*.xlsx")))
        self.client.post("/logout")

    def test_product_export_embeds_main_image(self):
        from openpyxl import load_workbook
        from PIL import Image

        from app.database import upsert_product

        image_dir = self.root / "data" / "product_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path = image_dir / "K-EXPORT-IMG.png"
        Image.new("RGB", (80, 40), "white").save(image_path)

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-EXPORT-IMG",
                    "series": "TEST",
                    "item": "EXPORT IMAGE",
                    "oe_no_1": "EXPORT-IMAGE-001",
                    "models": "Tester",
                    "image_path": "data_product_images/K-EXPORT-IMG.png",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.post("/products/export", data={"status": "active", "export_format": "bld"})
        self.assertEqual(response.status_code, 200)

        workbook = load_workbook(io.BytesIO(response.data))
        sheet = workbook["产品目录"]
        row_index = next(row[0].row for row in sheet.iter_rows(min_row=2) if row[0].value == "K-EXPORT-IMG")

        self.assertIsNone(sheet.cell(row_index, 7).value)
        self.assertGreaterEqual(len(sheet._images), 1)
        self.assertGreaterEqual(sheet.row_dimensions[row_index].height, 62)
        workbook.close()
        response.close()

    def test_admin_homepage_shows_all_recent_outputs(self):
        output_root = self.root / "outputs"
        other_user_dir = output_root / "u99-other"
        other_user_dir.mkdir(parents=True, exist_ok=True)
        root_file = output_root / "old-root-result.xlsx"
        other_user_file = other_user_dir / "other-user-result.xlsx"
        catalog_file = output_root / "catalog-export-bld-history-sample.xlsx"
        material_file = output_root / "26年4月冲压生产计划260423料单.xlsx"
        root_file.write_bytes(b"legacy")
        other_user_file.write_bytes(b"other")
        catalog_file.write_bytes(b"catalog")
        material_file.write_bytes(b"materials")

        self.login()
        response = self.client.get("/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("操作用户", html)
        self.assertIn("old-root-result.xlsx", html)
        self.assertIn("other-user-result.xlsx", html)
        self.assertIn("other", html)
        self.assertNotIn("catalog-export-bld-history-sample.xlsx", html)
        self.assertNotIn("26年4月冲压生产计划260423料单.xlsx", html)

        response = self.client.get("/?history_q=other-user")
        html = response.get_data(as_text=True)
        self.assertIn("other-user-result.xlsx", html)
        self.assertNotIn("old-root-result.xlsx", html)

        response = self.client.get("/?history_q=other")
        html = response.get_data(as_text=True)
        self.assertIn("other-user-result.xlsx", html)
        self.assertNotIn("old-root-result.xlsx", html)

    def test_quick_oe_lookup_on_homepage(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004LB",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z000",
                    "models": "Sportage",
                    "image_path": "product_images/K6004LB.jpg",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/?quick_oe=55270-2Z000")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("快速号码查询", html)
        self.assertIn("K6004LB", html)
        self.assertIn("OE 精准命中", html)
        self.assertIn("data-quick-oe-image", html)
        self.assertIn('id="quick-oe-image-modal"', html)

    def test_quick_brand_code_lookup_on_homepage(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004BR",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z010",
                    "oe_no_2": "MOOG：K623123",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        for query in ["623123", "K623123", "MOOG：K623123"]:
            with self.subTest(query=query):
                response = self.client.get("/", query_string={"quick_oe": query})
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn("快速号码查询", html)
                self.assertIn("K6004BR", html)
                self.assertIn("品牌号码精准命中", html)

    def test_quick_bld_lookup_on_homepage(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-BLD-LOOKUP",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "BLDLOOKUP-OE",
                    "models": "Sportage",
                    "active": "1",
                },
                actor="tester",
            )

        self.login()
        response = self.client.get("/", query_string={"quick_oe": "K-BLD-LOOKUP"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("快速号码查询", html)
        self.assertIn("K-BLD-LOOKUP", html)
        self.assertIn("BLD NO. 精准命中", html)

    def test_single_pasted_code_keeps_quick_lookup(self):
        self.login()
        response = self.client.post("/match", data={"quick_oe": "55270-2Z000"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/?quick_oe=55270-2Z000"))

    def test_pasted_multiple_codes_generates_match_excel(self):
        from app.database import upsert_product
        from openpyxl import load_workbook

        products = [
            ("K54500L", "54500-2D000", "79.2"),
            ("K54501L", "54501-2D000", "39.6"),
            ("K54501A", "54501-A0000", "118.8"),
        ]
        with self.web.connect(self.web.DB_PATH) as conn:
            for bld_no, oe_no, price_cny in products:
                upsert_product(
                    conn,
                    {
                        "bld_no": bld_no,
                        "series": "HYUNDAI",
                        "item": "CONTROL ARM",
                        "oe_no_1": oe_no,
                        "models": "Elantra",
                        "price_cny": price_cny,
                        "active": "1",
                    },
                    actor="tester",
                )

        self.login()
        response = self.client.post(
            "/match",
            data={"quick_oe": "54500-2d000 54501-2d000 54501-a0000"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("匹配结果", html)
        self.assertIn("下载 Excel", html)
        self.assertIn("K54500L", html)
        self.assertIn("K54501L", html)
        self.assertIn("K54501A", html)
        self.assertIn("粘贴号码询价.xlsx", html)
        self.assertIn("含税单价", html)
        self.assertIn("¥79.20", html)
        self.assertIn("<td>1</td>", html)
        self.assertIn("<td>2</td>", html)
        self.assertIn("<td>3</td>", html)
        self.assertNotIn("<td>4</td>", html)
        self.assertIn('id="download-excel-modal"', html)
        self.assertIn('action="/match/download"', html)
        self.assertNotIn("返回上一步", html)

        upload_match = re.search(r'name="upload_path" value="([^"]+)"', html)
        output_match = re.search(r'name="output_name" value="([^"]+)"', html)
        self.assertIsNotNone(upload_match)
        self.assertIsNotNone(output_match)
        upload_path = upload_match.group(1)
        output_name = output_match.group(1)
        output_path = self.root / "outputs" / "u1-007" / output_name

        download = self.client.post(
            "/match/download",
            data={
                "upload_path": upload_path,
                "original_filename": "粘贴号码询价.xlsx",
                "output_name": output_name,
                "match_column": "",
                "price_mode": "usd",
                "exchange_rate": "7.2",
            },
        )
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertTrue(output_path.exists())

        generated = load_workbook(output_path)
        sheet = generated.active
        self.assertEqual(sheet.cell(1, 1).value, "OE号")
        self.assertEqual(sheet.cell(1, 2).value, "BLD NO.")
        self.assertEqual(sheet.cell(1, 3).value, "美金价")
        self.assertEqual(sheet.cell(1, 4).value, "匹配说明")
        self.assertEqual(sheet.cell(2, 1).value, "54500-2d000")
        self.assertEqual(sheet.cell(2, 2).value, "K54500L")
        self.assertEqual(sheet.cell(2, 3).value, 10)
        self.assertEqual(sheet.cell(3, 2).value, "K54501L")
        self.assertEqual(sheet.cell(3, 3).value, 5)
        self.assertEqual(sheet.cell(4, 2).value, "K54501A")
        self.assertEqual(sheet.cell(4, 3).value, 15)
        generated.close()

    def test_pasted_multiple_bld_codes_generates_match_excel(self):
        from app.database import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            for bld_no in ["K-BLD-BATCH-1", "K-BLD-BATCH-2"]:
                upsert_product(
                    conn,
                    {
                        "bld_no": bld_no,
                        "series": "HYUNDAI",
                        "item": "CONTROL ARM",
                        "oe_no_1": f"{bld_no}-OE",
                        "models": "Elantra",
                        "active": "1",
                    },
                    actor="tester",
                )

        self.login()
        response = self.client.post("/match", data={"quick_oe": "K-BLD-BATCH-1 K-BLD-BATCH-2"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("匹配结果", html)
        self.assertIn("K-BLD-BATCH-1", html)
        self.assertIn("K-BLD-BATCH-2", html)
        self.assertIn("BLD NO. 精准命中", html)

    def test_uploaded_inquiry_can_export_tax_price(self):
        from app.database import upsert_product
        from openpyxl import Workbook, load_workbook

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "KPRICE01",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "PRICE-001",
                    "models": "Elantra",
                    "price_cny": "88.8",
                    "active": "1",
                },
                actor="tester",
            )

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["OE号"])
        sheet.append(["PRICE-001"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        self.login()
        response = self.client.post(
            "/match",
            data={"inquiry": (buffer, "price-export.xlsx")},
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("选择匹配列", html)
        self.assertIn("没有识别到明确的 OE 号码表头", html)
        self.assertNotIn("KPRICE01", html)
        self.assertNotIn('id="download-excel-modal"', html)

        upload_match = re.search(r'name="upload_path" value="([^"]+)"', html)
        output_match = re.search(r'name="output_name" value="([^"]+)"', html)
        self.assertIsNotNone(upload_match)
        self.assertIsNotNone(output_match)
        upload_path = upload_match.group(1)
        output_name = output_match.group(1)
        output_path = self.root / "outputs" / "u1-007" / output_name

        result = self.client.post(
            "/match/column",
            data={
                "upload_path": upload_path,
                "original_filename": "price-export.xlsx",
                "output_name": output_name,
                "match_column": "0",
            },
        )
        result_html = result.get_data(as_text=True)

        self.assertEqual(result.status_code, 200)
        self.assertIn("KPRICE01", result_html)
        self.assertIn("¥88.80", result_html)
        self.assertIn('id="download-excel-modal"', result_html)
        self.assertIn("返回上一步", result_html)

        download = self.client.post(
            "/match/download",
            data={
                "upload_path": upload_path,
                "original_filename": "price-export.xlsx",
                "output_name": output_name,
                "match_column": "0",
                "price_mode": "tax",
            },
        )
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertTrue(output_path.exists())

        generated = load_workbook(output_path)
        sheet = generated.active
        self.assertEqual(sheet.cell(1, 2).value, "BLD NO.")
        self.assertEqual(sheet.cell(1, 3).value, "含税单价")
        self.assertEqual(sheet.cell(1, 4).value, "匹配说明")
        self.assertEqual(sheet.cell(2, 2).value, "KPRICE01")
        self.assertEqual(sheet.cell(2, 3).value, 88.8)
        generated.close()

    def test_item_header_with_code_values_prompts_for_match_column(self):
        from app.database import upsert_product
        from openpyxl import Workbook

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "KPIKA01",
                    "series": "HYUNDAI",
                    "item": "LOWER ARM",
                    "oe_no_1": "TST545012B000",
                    "models": "SANTA FE 2006",
                    "price_cny": "66",
                    "active": "1",
                },
                actor="tester",
            )

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["PIKA BOOKING ORDER-200426"])
        sheet.append([])
        sheet.append(["SN", "ITEM", "DESCRIPTION", "QTY", "PRICE", "PICTURE", "BRAND"])
        sheet.append(["1", "TST545012B000 ", "LOWER ARM-HYUNDAI SANTA FE 2006", 60, None, None, "L-TGL"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        self.login()
        response = self.client.post(
            "/match",
            data={"inquiry": (buffer, "pika-order.xlsx")},
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("选择匹配列", html)
        self.assertIn("没有识别到明确的 OE 号码表头", html)

        upload_match = re.search(r'name="upload_path" value="([^"]+)"', html)
        output_match = re.search(r'name="output_name" value="([^"]+)"', html)
        self.assertIsNotNone(upload_match)
        self.assertIsNotNone(output_match)

        result = self.client.post(
            "/match/column",
            data={
                "upload_path": upload_match.group(1),
                "original_filename": "pika-order.xlsx",
                "output_name": output_match.group(1),
                "match_column": "1",
            },
        )
        result_html = result.get_data(as_text=True)

        self.assertEqual(result.status_code, 200)
        self.assertIn("共 1 行，命中 1 行，未找到 0 行", result_html)
        self.assertIn("KPIKA01", result_html)
        self.assertIn("¥66.00", result_html)
        self.assertIn("<td>4</td>", result_html)
        self.assertNotIn("<td>3</td>", result_html)

    def test_catalog_import_recognizes_chinese_brand_number_header(self):
        from app.matcher import ProductCatalog
        from openpyxl import Workbook

        for header in ["品牌号码", "Other Reference"]:
            with self.subTest(header=header):
                workbook = Workbook()
                sheet = workbook.active
                sheet.append(["BLD NO.", "品牌", "产品名称", "OE Reference", header, "车型"])
                sheet.append(["K6004CN", "HYUNDAI", "CONTROL ARM", "55270-2Z020", "BRAND-CN-55270", "Sportage"])
                catalog_path = self.root / f"catalog-brand-number-{header.replace(' ', '-').lower()}.xlsx"
                workbook.save(catalog_path)

                catalog = ProductCatalog.from_excel(catalog_path)
                match = catalog.match("", "BRAND-CN-55270")

                self.assertIsNotNone(match)
                self.assertEqual(match.bld_no, "K6004CN")
                self.assertEqual(match.reason, "品牌号码精准命中")

    def test_manual_column_result_defers_excel_until_download(self):
        from app.database import upsert_product
        from openpyxl import Workbook, load_workbook

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K6004LC",
                    "series": "HYUNDAI",
                    "item": "CONTROL ARM",
                    "oe_no_1": "55270-2Z001",
                    "models": "Sportage",
                    "price_cny": "55",
                    "active": "1",
                },
                actor="tester",
            )
            product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K6004LC",)).fetchone()

        self.login()
        drawing_upload = self.client.post(
            f"/products/{product['id']}/drawing",
            data={"drawing": (io.BytesIO(b"%PDF-1.4\nK6004LC drawing\n%%EOF"), "K6004LC.pdf")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(drawing_upload.status_code, 302)

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["客户号码", "数量"])
        sheet.append(["55270-2Z001", 1])
        buffer = io.BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        response = self.client.post(
            "/match",
            data={"inquiry": (buffer, "manual-column.xlsx")},
            content_type="multipart/form-data",
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("选择匹配列", html)
        self.assertIn("返回上一步", html)
        self.assertNotIn("返回首页", html)
        upload_match = re.search(r'name="upload_path" value="([^"]+)"', html)
        output_match = re.search(r'name="output_name" value="([^"]+)"', html)
        self.assertIsNotNone(upload_match)
        self.assertIsNotNone(output_match)
        upload_path = upload_match.group(1)
        output_name = output_match.group(1)
        output_path = self.root / "outputs" / "u1-007" / output_name

        result = self.client.post(
            "/match/column",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "output_name": output_name,
                "match_column": "0",
            },
        )
        result_html = result.get_data(as_text=True)

        self.assertEqual(result.status_code, 200)
        self.assertIn("Excel 文件将在点击下载时生成", result_html)
        self.assertIn("下载 Excel", result_html)
        self.assertIn("下载图纸包", result_html)
        self.assertIn("返回上一步", result_html)
        self.assertNotIn("返回首页", result_html)
        self.assertIn("K6004LC", result_html)
        self.assertIn("¥55.00", result_html)
        self.assertIn('id="download-excel-modal"', result_html)
        self.assertIn('name="price_mode"', result_html)
        self.assertFalse(output_path.exists())

        drawing_zip = self.client.post(
            "/match/drawings/download",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "match_column": "0",
            },
        )
        self.assertEqual(drawing_zip.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(drawing_zip.get_data())) as archive:
            self.assertIn("K6004LC_55270-2Z001.pdf", archive.namelist())
        drawing_zip.close()

        back = self.client.post(
            "/match/column/back",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "output_name": output_name,
                "match_column": "0",
            },
        )
        back_html = back.get_data(as_text=True)
        self.assertEqual(back.status_code, 200)
        self.assertIn("选择匹配列", back_html)
        self.assertIn("返回上一步", back_html)
        self.assertNotIn("返回首页", back_html)
        self.assertRegex(back_html, r'<option value="0"[^>]*selected')

        download = self.client.post(
            "/match/download",
            data={
                "upload_path": upload_path,
                "original_filename": "manual-column.xlsx",
                "output_name": output_name,
                "match_column": "0",
                "price_mode": "tax",
            },
        )
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertTrue(output_path.exists())

        generated = load_workbook(output_path)
        generated_sheet = generated.active
        self.assertEqual(generated_sheet.cell(1, 3).value, "BLD NO.")
        self.assertEqual(generated_sheet.cell(1, 4).value, "含税单价")
        self.assertEqual(generated_sheet.cell(1, 5).value, "匹配说明")
        self.assertEqual(generated_sheet.cell(2, 3).value, "K6004LC")
        self.assertEqual(generated_sheet.cell(2, 4).value, 55)
        generated.close()

    def test_uploaded_files_are_scoped_to_user(self):
        self.login()
        response = self.client.post(
            "/prices/import/preview",
            data={"price_file": (io.BytesIO(b"not a real workbook"), "same-name.xlsx")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        uploads = list((self.root / "uploads" / "u1-007").glob("price-*-007-same-name.xlsx"))
        self.assertEqual(len(uploads), 1)

    def test_import_lock_blocks_parallel_imports(self):
        from app.locks import ImportLockError, import_lock

        with import_lock("tester", "测试导入"):
            with self.assertRaises(ImportLockError):
                with import_lock("other", "第二个导入"):
                    pass


if __name__ == "__main__":
    unittest.main()
