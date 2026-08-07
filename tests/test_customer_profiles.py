from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import connect
from app.modules.customers.domain import CustomerValidationError
from app.modules.customers.infrastructure import QuoteCustomerReader
from app.modules.customers.service import CustomerService
from app.modules.customers.web import _owners
from app.modules.quotes.repository import SQLiteQuoteUnitOfWork
from app.modules.quotes.service import QuoteService


class CustomerProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "customer-profiles.sqlite3"

        def connection_factory():
            return connect(self.db_path)

        self.connection_factory = connection_factory
        with connection_factory():
            pass
        quote_service = QuoteService(
            lambda: SQLiteQuoteUnitOfWork(self.db_path),
            object(),
            object(),
            object(),
            object(),
            object(),
        )
        self.service = CustomerService(
            connection_factory,
            QuoteCustomerReader(lambda: quote_service),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profile_fields_status_and_active_lookup(self) -> None:
        customer = self.service.create("宁波多迦", actor="tester")
        renamed = self.service.rename(
            customer.id,
            "宁波多迦汽车部件",
            reason="客户主体名称已更新",
            actor="tester",
        )
        updated = self.service.update_code(
            customer.id,
            "C-001",
            reason="按客户编码规则登记",
            actor="tester",
        )
        updated = self.service.update_owner(customer.id, "007", actor="tester")
        self.assertEqual(renamed.name, "宁波多迦汽车部件")
        self.assertEqual(updated.name, "宁波多迦汽车部件")
        self.assertEqual(updated.code, "C-001")
        self.assertEqual(updated.owner_username, "007")
        self.assertEqual(self.service.lookup("多迦")[0].id, customer.id)

        inactive = self.service.set_status(customer.id, "inactive", actor="tester")
        self.assertEqual(inactive.status, "inactive")
        self.assertEqual(self.service.lookup("多迦"), [])
        self.assertIsNone(self.service.find_by_name("宁波多迦汽车部件"))
        self.assertEqual(self.service.get(customer.id).name, "宁波多迦汽车部件")

        active = self.service.set_status(customer.id, "active", actor="tester")
        self.assertEqual(active.status, "active")
        self.assertIsNotNone(self.service.find_by_name("宁波多迦汽车部件"))

    def test_customer_codes_are_optional_but_unique(self) -> None:
        first = self.service.create("客户甲", actor="tester")
        second = self.service.create("客户乙", actor="tester")
        self.service.update_code(first.id, "ACME", reason="建立客户编码", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "已被使用"):
            self.service.update_code(second.id, "acme", reason="建立客户编码", actor="tester")

    def test_inactive_current_owner_is_preserved_until_owner_changes(self) -> None:
        customer = self.service.create("负责人测试客户", actor="tester")
        self.service.update_owner(customer.id, "former-owner", actor="tester")
        guarded_service = CustomerService(
            self.connection_factory,
            self.service.business_reader,
            owner_validator=lambda username: username == "active-owner",
        )

        updated = guarded_service.rename(
            customer.id,
            "负责人测试客户（更新）",
            reason="客户名称更新",
            actor="tester",
        )
        self.assertEqual(updated.owner_username, "former-owner")
        with self.assertRaisesRegex(CustomerValidationError, "不存在或已停用"):
            guarded_service.update_owner(customer.id, "another-inactive-owner", actor="tester")

    def test_owner_options_keep_current_inactive_or_missing_account(self) -> None:
        class FakeAdminService:
            @staticmethod
            def users():
                return (
                    [
                        {"username": "active-owner", "display_name": "在职负责人", "active": 1},
                        {"username": "former-owner", "display_name": "离职负责人", "active": 0},
                        {"username": "other-inactive", "display_name": "其他停用账号", "active": 0},
                    ],
                    None,
                )

        with patch("app.modules.admin.factory.get_admin_service", return_value=FakeAdminService()):
            inactive_options = _owners(current_username="former-owner")
            missing_options = _owners(current_username="missing-owner")

        self.assertEqual(
            [owner["username"] for owner in inactive_options],
            ["active-owner", "former-owner"],
        )
        self.assertFalse(inactive_options[1]["active"])
        self.assertEqual(missing_options[-1]["username"], "missing-owner")
        self.assertTrue(missing_options[-1]["missing"])

    def test_customer_cannot_be_physically_deleted_without_business_history(self) -> None:
        customer = self.service.create("无业务记录客户", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "请改为停用"):
            self.service.delete(customer.id, actor="tester")
        self.assertEqual(self.service.get(customer.id).status, "active")

    def test_customer_rename_is_compensated_when_quote_sync_fails(self) -> None:
        class FailingQuoteReader:
            def quote_stats(self, customers):
                return {}

            def quote_history(self, customer_id, customer_name, *, limit=50):
                return []

            def rename_customer_references(self, customer_id, old_name, new_name):
                raise RuntimeError("quote store unavailable")

        service = CustomerService(self.connection_factory, FailingQuoteReader())
        customer = service.create("改名前客户", actor="tester")
        with self.assertRaisesRegex(RuntimeError, "quote store unavailable"):
            service.rename(customer.id, "改名后客户", reason="客户名称更新", actor="tester")
        self.assertEqual(service.get(customer.id).name, "改名前客户")

    def test_identity_changes_require_reason_change_value_and_write_specific_audit(self) -> None:
        customer = self.service.create("身份资料客户", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "请填写变更原因"):
            self.service.rename(customer.id, "身份资料客户（新）", reason="", actor="tester")
        with self.assertRaisesRegex(CustomerValidationError, "未发生变化"):
            self.service.update_code(customer.id, "", reason="确认编号", actor="tester")

        self.service.rename(customer.id, "身份资料客户（新）", reason="客户合同主体调整", actor="tester")
        self.service.update_code(customer.id, "ID-001", reason="建立统一编码", actor="tester")
        with connect(self.db_path) as connection:
            events = connection.execute(
                "SELECT action, detail, actor FROM audit_logs WHERE target_type = 'customer' ORDER BY id"
            ).fetchall()
        self.assertEqual([event["action"] for event in events[-2:]], ["变更客户名称", "变更客户编号"])
        self.assertIn("原因：客户合同主体调整", events[-2]["detail"])
        self.assertIn("原因：建立统一编码", events[-1]["detail"])
        self.assertEqual([event["actor"] for event in events[-2:]], ["tester", "tester"])

    def test_contacts_crud_and_single_primary_contact(self) -> None:
        customer = self.service.create("联系人测试客户", actor="tester")
        first = self.service.save_contact(
            customer.id,
            {"name": "张采购", "role": "采购", "phone": "13800000000"},
            actor="tester",
        )
        self.assertTrue(first.is_primary)

        second = self.service.save_contact(
            customer.id,
            {"name": "李技术", "role": "技术", "email": "li@example.com", "is_primary": "1"},
            actor="tester",
        )
        contacts = self.service.detail(customer.id)["contacts"]
        self.assertEqual([contact.name for contact in contacts], ["李技术", "张采购"])
        self.assertEqual(sum(contact.is_primary for contact in contacts), 1)

        changed = self.service.save_contact(
            customer.id,
            {"name": "李工", "role": "技术", "email": "li@example.com", "is_primary": "1"},
            actor="tester",
            contact_id=second.id,
        )
        self.assertEqual(changed.name, "李工")
        self.service.delete_contact(customer.id, first.id, actor="tester")
        remaining = self.service.detail(customer.id)["contacts"]
        self.assertEqual([contact.name for contact in remaining], ["李工"])

    def test_unchecking_or_deleting_primary_promotes_another_contact(self) -> None:
        customer = self.service.create("主要联系人不变量客户", actor="tester")
        first = self.service.save_contact(customer.id, {"name": "第一联系人"}, actor="tester")
        second = self.service.save_contact(customer.id, {"name": "第二联系人"}, actor="tester")

        demoted = self.service.save_contact(
            customer.id,
            {"name": first.name},
            actor="tester",
            contact_id=first.id,
        )
        contacts = self.service.detail(customer.id)["contacts"]
        self.assertFalse(demoted.is_primary)
        self.assertEqual([contact.id for contact in contacts if contact.is_primary], [second.id])

        self.service.delete_contact(customer.id, second.id, actor="tester")
        remaining = self.service.detail(customer.id)["contacts"]
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0].is_primary)

        kept_primary = self.service.save_contact(
            customer.id,
            {"name": remaining[0].name},
            actor="tester",
            contact_id=remaining[0].id,
        )
        self.assertTrue(kept_primary.is_primary)

    def test_list_summary_counts_quote_numbers_and_products(self) -> None:
        customer = self.service.create("报价统计客户", actor="tester")
        with connect(self.db_path) as connection:
            connection.executemany(
                """
                INSERT INTO quote_records (
                  customer_id, customer_name, bld_no, product_model, currency,
                  quote_date, quoted_by, source_type, sync_id, quote_no, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'CNY', ?, '007', 'manual', ?, ?, ?, ?)
                """,
                [
                    (customer.id, customer.name, "K100", "K100", "2026-07-01", "quote-row-1", "Q001", "2026-07-01", "2026-07-01"),
                    (customer.id, customer.name, "K101", "K101", "2026-07-01", "quote-row-2", "Q001", "2026-07-01", "2026-07-01"),
                    (customer.id, customer.name, "K100", "K100", "2026-07-15", "quote-row-3", "Q002", "2026-07-15", "2026-07-15"),
                ],
            )
            connection.commit()

        summary = self.service.list_summaries(query="报价统计", status="active")[0]
        self.assertEqual(summary.quote_count, 2)
        self.assertEqual(summary.quoted_product_count, 2)
        self.assertEqual(summary.latest_quote_date, "2026-07-15")
        history = self.service.detail(customer.id)["quotes"]
        self.assertEqual([item.quote_no for item in history], ["Q002", "Q001"])
        self.assertEqual(history[1].line_count, 2)

    def test_list_filters_are_bounded_and_accept_owner_alias_matches(self) -> None:
        customer = self.service.create("负责人筛选客户", actor="tester")
        self.service.update_owner(customer.id, "007", actor="tester")

        self.assertEqual(
            self.service.list_summaries(query="x" * 201, status="invalid" * 10),
            [],
        )
        by_display_name = self.service.list_summaries(
            query="管理员",
            owner_usernames=("007",),
        )
        by_username = self.service.list_summaries(query="007")
        self.assertEqual([summary.customer.id for summary in by_display_name], [customer.id])
        self.assertEqual([summary.customer.id for summary in by_username], [customer.id])


if __name__ == "__main__":
    unittest.main()
