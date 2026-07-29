from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.database import connect
from app.modules.contracts.repository import SQLiteContractUnitOfWork
from app.modules.contracts.infrastructure import QuoteSelectionTokenAdapter
from app.modules.contracts.quote_contract_source import QuoteContractSource
from app.modules.contracts.sales_pdf import _sales_currency_text
from app.modules.contracts.service import ContractService
from app.modules.quotes.domain import QuoteValidationError
from app.modules.quotes.repository import SQLiteQuoteUnitOfWork
from app.modules.quotes.service import QuoteService


class _ImportPort:
    def parse(self, _path, *, customer_name, currency):
        return {"rows": [], "counts": {"total": 0, "valid": 0, "invalid": 0}}

    def encode(self, _rows):
        return "[]"

    def decode(self, _payload):
        return []


class _ImportLock:
    @contextmanager
    def __call__(self, _owner, _purpose):
        yield


class _CatalogDirectory:
    def exists(self, _bld_no: str) -> bool:
        return True


class _CustomerDirectory:
    def __init__(self, customer_ids: dict[str, int]) -> None:
        self.customer_ids = customer_ids
        self.active_ids = set(customer_ids.values())

    def exists(self, customer_name: str) -> bool:
        customer_id = self.customer_ids.get(customer_name)
        return customer_id is not None and customer_id in self.active_ids

    def find_id(self, customer_name: str) -> int | None:
        return self.customer_ids.get(customer_name) if self.exists(customer_name) else None

    def find_active_id(self, customer_id: int | None, customer_name: str) -> int | None:
        resolved = customer_id if customer_id is not None else self.customer_ids.get(customer_name)
        return resolved if resolved in self.active_ids else None

    def deactivate(self, customer_name: str) -> None:
        self.active_ids.discard(self.customer_ids[customer_name])


class _Product:
    def __init__(self, bld_no: str) -> None:
        self.bld_no = bld_no

    def web_payload(self) -> dict[str, object]:
        return {
            "bld_no": self.bld_no,
            "oe_no_1": f"OE-{self.bld_no}",
            "item": "Control Arm",
            "models": "Test Car",
        }


class _ProductService:
    def find_by_bld(self, bld_no: str):
        return _Product(bld_no) if bld_no else None


class _QuoteSource:
    def __init__(self, quote_service: QuoteService) -> None:
        self.quote_service = quote_service

    def build_draft(self, quote_no, quote_ids, language):
        return self.quote_service.sales_contract_draft(quote_no, quote_ids, language)


class _PdfAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.contract: dict | None = None

    def generate(self, _kind: str, contract: dict, output_path: Path) -> None:
        if self.fail:
            raise RuntimeError("pdf failed")
        self.contract = contract
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"%PDF-test")


class QuoteToSalesContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "test.sqlite3"
        with connect(self.database_path) as connection:
            connection.executemany(
                "INSERT INTO customers (name, sync_id) VALUES (?, ?)",
                (("Customer A", "customer-a"), ("Customer B", "customer-b")),
            )
            rows = connection.execute("SELECT id, name FROM customers").fetchall()
            self.customer_ids = {str(row["name"]): int(row["id"]) for row in rows}
        self.customer_directory = _CustomerDirectory(self.customer_ids)
        self.quote_service = QuoteService(
            lambda: SQLiteQuoteUnitOfWork(self.database_path),
            _ImportPort(),
            _ImportLock(),
            _CatalogDirectory(),
            self.customer_directory,
        )
        self.selection_token = QuoteSelectionTokenAdapter("quote-contract-test-secret")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _quote(*, customer: str = "Customer A", bld_no: str, currency: str = "USD", **prices):
        return {
            "customer_name": customer,
            "bld_no": bld_no,
            "customer_product_code": f"CUSTOMER-{bld_no}",
            "currency": currency,
            "quote_date": "2026-07-29",
            "remark": f"Remark {bld_no}",
            "source_type": "manual",
            **prices,
        }

    def _quote_batch(self):
        records, skipped, quote_no = self.quote_service.create_many(
            [
                self._quote(bld_no="BLD-001", tax_price="10.50"),
                self._quote(bld_no="BLD-002", tax_price="12.75"),
            ],
            actor="sales-user",
        )
        self.assertEqual(skipped, 0)
        return records, quote_no

    def _contract_service(
        self,
        pdf: _PdfAdapter | None = None,
        *,
        product_service=None,
    ) -> tuple[ContractService, _PdfAdapter, Path]:
        document_root = self.root / "outputs"
        adapter = pdf or _PdfAdapter()
        service = ContractService(
            lambda: SQLiteContractUnitOfWork(self.database_path),
            product_service or _ProductService(),
            adapter,
            lambda _product: None,
            _QuoteSource(self.quote_service),
            self.selection_token,
            customer_directory=self.customer_directory,
            document_root=document_root,
        )
        return service, adapter, document_root

    @staticmethod
    def _source_form(records, quote_no: str, source_token: str, *, language: str = "zh-CN") -> MultiDict:
        values = [
            ("contract_no", "XS-FROM-QUOTE-001"),
            ("contract_date", "2026-07-29"),
            ("language", language),
            ("currency", "USD"),
            ("source_quote_no", quote_no),
            ("source_quote_token", source_token),
            ("buyer_name", "玉环博莱德机械有限公司"),
            ("supplier_name", "Customer A"),
        ]
        for index, record in enumerate(records, start=1):
            price = record.tax_price if record.tax_price is not None else record.net_price
            values.extend(
                [
                    ("source_quote_row_id[]", str(record.id)),
                    ("source_quote_row_version[]", str(record.version)),
                    ("product_code[]", record.bld_no),
                    ("customer_code[]", record.customer_product_code),
                    ("oe_no[]", ""),
                    ("product_name[]", ""),
                    ("models[]", ""),
                    ("quantity[]", str(index + 1)),
                    ("unit_price[]", str(price)),
                    ("delivery_date[]", ""),
                    ("item_note[]", record.remark),
                ]
            )
        return MultiDict(values)

    def test_quote_selection_builds_chinese_draft_without_quantity_or_delivery(self) -> None:
        records, quote_no = self._quote_batch()

        draft = self.quote_service.sales_contract_draft(
            quote_no,
            [str(record.id) for record in records],
            "zh-CN",
        )

        self.assertEqual(draft["customer_id"], self.customer_ids["Customer A"])
        self.assertEqual(draft["customer_name"], "Customer A")
        self.assertEqual(draft["currency"], "USD")
        self.assertEqual(draft["price_basis"], "tax")
        self.assertEqual([item["quantity"] for item in draft["items"]], ["", ""])
        self.assertEqual([item["delivery_date"] for item in draft["items"]], ["", ""])
        self.assertEqual([item["unit_price"] for item in draft["items"]], ["10.5000", "12.7500"])

        summaries = self.quote_service.customer_summaries([(self.customer_ids["Customer A"], "Customer A")])
        self.assertEqual(summaries[self.customer_ids["Customer A"]]["quote_count"], 1)
        self.assertEqual(summaries[self.customer_ids["Customer A"]]["product_count"], 2)
        history = self.quote_service.customer_quote_history(
            self.customer_ids["Customer A"],
            "Customer A",
        )
        self.assertEqual(history[0]["quote_no"], quote_no)
        self.assertEqual(history[0]["line_count"], 2)
        updated = self.quote_service.rename_customer_references(
            self.customer_ids["Customer A"],
            "Customer A",
            "Customer A Renamed",
        )
        self.assertEqual(updated, 2)
        self.assertEqual(
            {record.customer_name for record in self.quote_service.records_by_quote_no(quote_no)},
            {"Customer A Renamed"},
        )

    def test_language_customer_currency_and_price_guards_are_server_side(self) -> None:
        records, quote_no = self._quote_batch()
        selected = [record.id for record in records]
        with self.assertRaisesRegex(QuoteValidationError, "英文版"):
            self.quote_service.sales_contract_draft(quote_no, selected, "en-US")
        with self.assertRaisesRegex(QuoteValidationError, "至少选择"):
            self.quote_service.sales_contract_draft(quote_no, [], "zh-CN")
        with self.assertRaisesRegex(QuoteValidationError, "不能为负数"):
            self.quote_service.create(
                self._quote(bld_no="BLD-NEGATIVE", tax_price="-10"),
                actor="sales-user",
            )

        ambiguous = self.quote_service.create(
            self._quote(bld_no="BLD-AMBIGUOUS", tax_price="20", net_price="18"),
            actor="sales-user",
        )
        with self.assertRaisesRegex(QuoteValidationError, "同时存在含税价和不含税价"):
            self.quote_service.sales_contract_draft(ambiguous.quote_no, [ambiguous.id], "zh-CN")

        mixed, _skipped, mixed_quote_no = self.quote_service.create_many(
            [
                self._quote(bld_no="BLD-MIX-1", tax_price="10"),
                self._quote(customer="Customer B", bld_no="BLD-MIX-2", currency="EUR", tax_price="11"),
            ],
            actor="sales-user",
        )
        with self.assertRaisesRegex(QuoteValidationError, "同一个客户"):
            self.quote_service.sales_contract_draft(mixed_quote_no, [record.id for record in mixed], "zh-CN")

        mixed_currency, _skipped, currency_quote_no = self.quote_service.create_many(
            [
                self._quote(bld_no="BLD-CURRENCY-1", currency="USD", tax_price="10"),
                self._quote(bld_no="BLD-CURRENCY-2", currency="EUR", tax_price="11"),
            ],
            actor="sales-user",
        )
        with self.assertRaisesRegex(QuoteValidationError, "同一种币种"):
            self.quote_service.sales_contract_draft(
                currency_quote_no,
                [record.id for record in mixed_currency],
                "zh-CN",
            )

        missing_price = self.quote_service.create(
            self._quote(bld_no="BLD-NO-PRICE", tax_price="15"),
            actor="sales-user",
        )
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE quote_records SET tax_price = NULL, net_price = NULL WHERE id = ?",
                (missing_price.id,),
            )
            connection.commit()
        with self.assertRaisesRegex(QuoteValidationError, "缺少有效价格"):
            self.quote_service.sales_contract_draft(missing_price.quote_no, [missing_price.id], "zh-CN")

        contract = {"items": [{"quantity": Decimal("2"), "delivery_date": ""}]}
        source = {
            "source_quote_no": "Q-NEGATIVE",
            "customer_id": self.customer_ids["Customer A"],
            "customer_name": "Customer A",
            "currency": "USD",
            "price_basis": "tax",
            "items": [
                {
                    "quote_id": 1,
                    "quote_version": 1,
                    "product_code": "BLD-NEGATIVE",
                    "unit_price": "-10.0000",
                    "price_kind": "tax",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "不能为负数"):
            QuoteContractSource._apply_source_items(contract, source)

        for bld_no, stored_price, message in (
            ("BLD-INFINITE", float("inf"), "有限数字"),
            ("BLD-TOO-LARGE", 10_000_000_000_000.0, "数值过大"),
        ):
            record = self.quote_service.create(
                self._quote(bld_no=bld_no, tax_price="10"),
                actor="sales-user",
            )
            with connect(self.database_path) as connection:
                connection.execute(
                    "UPDATE quote_records SET tax_price = ? WHERE id = ?",
                    (stored_price, record.id),
                )
                connection.commit()
            with self.assertRaisesRegex(QuoteValidationError, message):
                self.quote_service.sales_contract_draft(record.quote_no, [record.id], "zh-CN")

    def test_successful_generation_records_quote_link_and_failed_pdf_does_not(self) -> None:
        records, quote_no = self._quote_batch()
        contract_service, pdf, document_root = self._contract_service()
        user_output = document_root / "u1-sales-user"
        context = contract_service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in records],
            language="zh-CN",
        )
        self.assertEqual(context["contract_draft"]["customer_name"], "Customer A")
        self.assertEqual(context["contract_draft"]["currency"], "USD")
        self.assertEqual(context["contract_rows"][0]["quantity"], "")
        self.assertEqual(context["contract_rows"][0]["delivery_date"], "")
        self.assertEqual(context["contract_rows"][0]["oe_no"], "OE-BLD-001")
        form = self._source_form(records, quote_no, str(context["contract_draft"]["source_token"]))
        form.setlist("supplier_name", ["Tampered Customer"])
        form.setlist("currency", ["NOT-A-CURRENCY"])
        form.setlist("product_code[]", ["BLD-HACKED", "BLD-002-HACKED"])
        form.setlist("customer_code[]", ["CUSTOMER-HACKED", "CUSTOMER-HACKED-2"])
        form.setlist("unit_price[]", ["999.99", "888.88"])
        form.setlist("item_note[]", ["Tampered one", "Tampered two"])

        output = contract_service.generate("sales", form, output_root=user_output, actor="sales-user")

        self.assertTrue(output.is_file())
        self.assertEqual(pdf.contract["currency"], "USD")
        self.assertEqual(pdf.contract["customer_name"], "Customer A")
        self.assertEqual([item["product_code"] for item in pdf.contract["items"]], ["BLD-001", "BLD-002"])
        self.assertEqual([item["unit_price"] for item in pdf.contract["items"]], [Decimal("10.5000"), Decimal("12.7500")])
        self.assertEqual(
            [item["customer_code"] for item in pdf.contract["items"]],
            ["CUSTOMER-BLD-001", "CUSTOMER-BLD-002"],
        )
        self.assertEqual(
            [item["note"] for item in pdf.contract["items"]],
            ["Remark BLD-001", "Remark BLD-002"],
        )
        documents = contract_service.documents_for_quote(quote_no)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["contract_no"], "XS-FROM-QUOTE-001")
        self.assertEqual(documents[0]["customer_id"], self.customer_ids["Customer A"])
        self.assertEqual(documents[0]["language"], "zh-CN")
        self.assertEqual(documents[0]["currency"], "USD")
        snapshot_json = str(documents[0]["source_snapshot_json"])
        snapshot = json.loads(snapshot_json)
        self.assertEqual(
            [(row["quote_id"], row["quote_version"]) for row in snapshot["rows"]],
            [(record.id, record.version) for record in records],
        )
        self.assertEqual([row["unit_price"] for row in snapshot["rows"]], ["10.5000", "12.7500"])
        self.assertEqual(
            documents[0]["source_snapshot_sha256"],
            hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(contract_service.document_path(int(documents[0]["id"]))[0], output.resolve())

        failing_service, _failed_pdf, _document_root = self._contract_service(_PdfAdapter(fail=True))
        failed_form = MultiDict(form)
        failed_form.setlist("contract_no", ["XS-FROM-QUOTE-FAIL"])
        with self.assertRaisesRegex(RuntimeError, "pdf failed"):
            failing_service.generate("sales", failed_form, output_root=user_output, actor="sales-user")
        self.assertEqual(len(contract_service.documents_for_quote(quote_no)), 1)
        self.assertEqual(list(document_root.rglob("*XS-FROM-QUOTE-FAIL*.pdf")), [])

    def test_quote_source_post_requires_explicit_supported_language(self) -> None:
        records, quote_no = self._quote_batch()
        service, _pdf, document_root = self._contract_service()
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in records],
            language="zh-CN",
        )
        token = str(context["contract_draft"]["source_token"])

        for language, message in (("", "请选择"), ("en-US", "英文版"), ("fr-FR", "无效")):
            with self.subTest(language=language):
                form = self._source_form(records, quote_no, token, language=language)
                with self.assertRaisesRegex(ValueError, message):
                    service.generate(
                        "sales",
                        form,
                        output_root=document_root / "u1-sales-user",
                        actor="sales-user",
                    )

        self.assertEqual(service.documents_for_quote(quote_no), [])
        self.assertEqual(list(document_root.rglob("*.pdf")), [])

    def test_manual_sales_contract_keeps_customer_association_after_rename(self) -> None:
        service, _pdf, document_root = self._contract_service()
        form = MultiDict(
            [
                ("contract_no", "XS-MANUAL-001"),
                ("contract_date", "2026-07-29"),
                ("language", "zh-CN"),
                ("currency", "CNY"),
                ("buyer_name", "玉环博莱德机械有限公司"),
                ("supplier_name", "Customer A"),
                ("product_code[]", "BLD-MANUAL"),
                ("quantity[]", "2"),
                ("unit_price[]", "10"),
            ]
        )

        service.generate(
            "sales",
            form,
            output_root=document_root / "u1-sales-user",
            actor="sales-user",
        )

        customer_id = self.customer_ids["Customer A"]
        before = service.documents_for_customer(customer_id, customer_name="Customer A")
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0]["customer_id"], customer_id)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE customers SET name = ? WHERE id = ?",
                ("Customer A Renamed", customer_id),
            )
            connection.commit()

        after = service.documents_for_customer(customer_id, customer_name="Customer A Renamed")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["contract_no"], "XS-MANUAL-001")

    def test_quote_source_rejects_row_identity_mismatch_and_concurrent_revision(self) -> None:
        records, quote_no = self._quote_batch()
        service, _pdf, document_root = self._contract_service()
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in records],
            language="zh-CN",
        )
        token = str(context["contract_draft"]["source_token"])
        mismatched = self._source_form(records, quote_no, token)
        mismatched.setlist("source_quote_row_id[]", [str(records[1].id), str(records[0].id)])
        with self.assertRaisesRegex(ValueError, "明细已变化"):
            service.generate(
                "sales",
                mismatched,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        self.quote_service.update(
            records[0].id,
            {"tax_price": "11.25"},
            actor="sales-user",
            expected_version=records[0].version,
        )
        stale = self._source_form(records, quote_no, token)
        with self.assertRaisesRegex(ValueError, "已被修订"):
            service.generate(
                "sales",
                stale,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        self.assertEqual(service.documents_for_quote(quote_no), [])
        self.assertEqual(list(document_root.rglob("*.pdf")), [])

    def test_same_version_source_change_and_customer_rename_invalidate_selection(self) -> None:
        records, quote_no = self._quote_batch()
        service, _pdf, document_root = self._contract_service()
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in records],
            language="zh-CN",
        )
        token = str(context["contract_draft"]["source_token"])
        same_version_form = self._source_form(records, quote_no, token)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE quote_records SET tax_price = ? WHERE id = ?",
                ("99.25", records[0].id),
            )
            connection.commit()
        with self.assertRaisesRegex(ValueError, "已变化"):
            service.generate(
                "sales",
                same_version_form,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        refreshed = self.quote_service.records_by_quote_no(quote_no)
        refreshed_context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in refreshed],
            language="zh-CN",
        )
        renamed_form = self._source_form(
            refreshed,
            quote_no,
            str(refreshed_context["contract_draft"]["source_token"]),
        )
        before_versions = {record.id: record.version for record in refreshed}
        self.quote_service.rename_customer_references(
            self.customer_ids["Customer A"],
            "Customer A",
            "Customer A Renamed",
        )
        renamed = self.quote_service.records_by_quote_no(quote_no)
        self.assertTrue(all(record.version == before_versions[record.id] + 1 for record in renamed))
        with self.assertRaisesRegex(ValueError, "已被修订"):
            service.generate(
                "sales",
                renamed_form,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        self.assertEqual(service.documents_for_quote(quote_no), [])
        self.assertEqual(list(document_root.rglob("*.pdf")), [])

    def test_inactive_customer_is_rejected_on_draft_and_rechecked_on_post(self) -> None:
        records, quote_no = self._quote_batch()
        service, _pdf, document_root = self._contract_service()
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in records],
            language="zh-CN",
        )
        form = self._source_form(records, quote_no, str(context["contract_draft"]["source_token"]))
        self.customer_directory.deactivate("Customer A")

        with self.assertRaisesRegex(QuoteValidationError, "已停用"):
            self.quote_service.sales_contract_draft(
                quote_no,
                [record.id for record in records],
                "zh-CN",
            )
        with self.assertRaisesRegex(QuoteValidationError, "已停用"):
            service.generate(
                "sales",
                form,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        self.assertEqual(service.documents_for_quote(quote_no), [])
        self.assertEqual(list(document_root.rglob("*.pdf")), [])

    def test_snapshot_and_pdf_share_one_catalog_enrichment(self) -> None:
        class EvolvingProduct(_Product):
            def __init__(self, bld_no: str, revision: int) -> None:
                super().__init__(bld_no)
                self.revision = revision

            def web_payload(self) -> dict[str, object]:
                return {
                    "bld_no": self.bld_no,
                    "oe_no_1": f"OE-{self.bld_no}-R{self.revision}",
                    "item": f"Control Arm R{self.revision}",
                    "models": f"Test Car R{self.revision}",
                }

        class EvolvingProductService:
            def __init__(self) -> None:
                self.calls = 0

            def find_by_bld(self, bld_no: str):
                self.calls += 1
                return EvolvingProduct(bld_no, 1 if self.calls <= 2 else 2)

        record = self.quote_service.create(
            self._quote(bld_no="BLD-ENRICH", tax_price="10.50"),
            actor="sales-user",
        )
        products = EvolvingProductService()
        service, pdf, document_root = self._contract_service(product_service=products)
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=record.quote_no,
            quote_ids=[record.id],
            language="zh-CN",
        )
        form = self._source_form([record], record.quote_no, str(context["contract_draft"]["source_token"]))

        service.generate(
            "sales",
            form,
            output_root=document_root / "u1-sales-user",
            actor="sales-user",
        )

        document = service.documents_for_quote(record.quote_no)[0]
        snapshot_item = json.loads(str(document["source_snapshot_json"]))["rows"][0]
        pdf_item = pdf.contract["items"][0]
        self.assertEqual(snapshot_item["oe_no"], pdf_item["oe_no"])
        self.assertEqual(snapshot_item["product_name"], pdf_item["product_name"])
        self.assertEqual(snapshot_item["models"], pdf_item["models"])
        self.assertEqual(products.calls, 2)

    def test_non_finite_and_huge_quantities_return_validation_errors(self) -> None:
        records, quote_no = self._quote_batch()
        service, _pdf, document_root = self._contract_service()
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=quote_no,
            quote_ids=[record.id for record in records],
            language="zh-CN",
        )
        token = str(context["contract_draft"]["source_token"])

        for quantity, message in (("NaN", "有限数字"), ("Infinity", "有限数字"), ("1e999999", "数值过大")):
            with self.subTest(quantity=quantity):
                form = self._source_form(records, quote_no, token)
                form.setlist("quantity[]", [quantity, "1"])
                with self.assertRaisesRegex(ValueError, message):
                    service.generate(
                        "sales",
                        form,
                        output_root=document_root / "u1-sales-user",
                        actor="sales-user",
                    )

        self.assertEqual(service.documents_for_quote(quote_no), [])
        self.assertEqual(list(document_root.rglob("*.pdf")), [])

    def test_line_and_total_amount_overflow_return_validation_errors(self) -> None:
        record = self.quote_service.create(
            self._quote(bld_no="BLD-AMOUNT-OVERFLOW", currency="CNY", tax_price="100000000"),
            actor="sales-user",
        )
        service, _pdf, document_root = self._contract_service()
        context = service.page_context(
            mode="sales",
            user_label="sales-user",
            output_reader=lambda _pattern, limit=200: [],
            history_type="sales",
            history_query="",
            source_quote_no=record.quote_no,
            quote_ids=[record.id],
            language="zh-CN",
        )
        source_form = self._source_form(
            [record],
            record.quote_no,
            str(context["contract_draft"]["source_token"]),
        )
        source_form.setlist("quantity[]", ["100000000"])
        with self.assertRaisesRegex(ValueError, "第 1 行金额数值过大"):
            service.generate(
                "sales",
                source_form,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        manual_form = MultiDict(
            [
                ("contract_no", "XS-TOTAL-OVERFLOW"),
                ("contract_date", "2026-07-29"),
                ("language", "zh-CN"),
                ("currency", "USD"),
                ("buyer_name", "玉环博莱德机械有限公司"),
                ("supplier_name", "Customer A"),
                ("product_code[]", "BLD-TOTAL-1"),
                ("product_code[]", "BLD-TOTAL-2"),
                ("quantity[]", "10000000"),
                ("quantity[]", "10000000"),
                ("unit_price[]", "50000000"),
                ("unit_price[]", "50000000"),
            ]
        )
        with self.assertRaisesRegex(ValueError, "合同合计金额数值过大"):
            service.generate(
                "sales",
                manual_form,
                output_root=document_root / "u1-sales-user",
                actor="sales-user",
            )

        self.assertEqual(service.documents_for_quote(record.quote_no), [])
        self.assertEqual(list(document_root.rglob("*.pdf")), [])

    def test_non_cny_pdf_labels_do_not_contain_rmb_wording_or_symbol(self) -> None:
        unit_heading, total_text = _sales_currency_text(
            {"currency": "USD", "total_amount": Decimal("25"), "total_amount_upper": "不应显示"}
        )
        self.assertEqual(unit_heading, "单价（USD）")
        self.assertEqual(total_text, "合计金额：USD 25.00")
        for forbidden in ("人民币", "大写", "¥", "元"):
            self.assertNotIn(forbidden, unit_heading + total_text)

        cny_heading, cny_total = _sales_currency_text(
            {"currency": "CNY", "total_amount": Decimal("25"), "total_amount_upper": "贰拾伍元整"}
        )
        self.assertEqual(cny_heading, "单价（元）")
        self.assertIn("大写", cny_total)
        self.assertIn("¥", cny_total)

    def test_contract_document_download_rejects_paths_outside_output_root(self) -> None:
        document_root = self.root / "outputs"
        document_root.mkdir(parents=True)
        outside = self.root / "outside.pdf"
        outside.write_bytes(b"%PDF-outside")
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO contract_documents
                  (contract_no, customer_name, file_path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                ("XS-UNSAFE", "Customer A", "../outside.pdf", "2026-07-29 12:00:00"),
            )
            document_id = int(cursor.lastrowid)
            connection.commit()
        service = ContractService(
            lambda: SQLiteContractUnitOfWork(self.database_path),
            _ProductService(),
            _PdfAdapter(),
            lambda _product: None,
            document_root=document_root,
        )
        self.assertIsNone(service.document_path(document_id))


if __name__ == "__main__":
    unittest.main()
