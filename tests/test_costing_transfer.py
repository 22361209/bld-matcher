from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.modules.costing.domain import CostingConflict, CostingValidationError
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing import transfer_repository, transfer_service
from app.modules.costing.transfer_service import CostingTransferService, snapshot_digest


class CostingTransferTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "source.sqlite3"
        self.target = Path(self.temporary.name) / "target.sqlite3"
        initialize_costing_database(self.source)
        initialize_costing_database(self.target)
        self.source_service = CostingService(self.source)
        self.target_service = CostingService(self.target)
        self.exporter = CostingTransferService(self.source)
        self.receiver = CostingTransferService(self.target)
        with sqlite3.connect(self.target) as conn:
            conn.execute("CREATE TABLE unrelated_products (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO unrelated_products VALUES (1, 'existing price')")
            conn.execute("""CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, action TEXT NOT NULL,
                         target_type TEXT NOT NULL, target_key TEXT NOT NULL, actor TEXT, detail TEXT, created_at TEXT NOT NULL)""")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def sheet(code="KTESTLA", **overrides):
        return {"code": code, "kind": "arm", "name": "摆臂", "models": "车型 A",
                "source_info": {"sheet": "摆臂成本核算", "row": 23},
                "items": [{"name": "材料", "amount": "100", "source_info": {"cell": "H23"}}], **overrides}

    def seed(self):
        result = self.source_service.import_records([self.sheet(), self.sheet("KTESTRA")], actor="原导入人",
                                                    references={"cartons": [{"code": "BOX", "unit_price": "4.50"}]})
        original = result["records"][0]
        self.source_service.update(original["id"], {"suggested_tax_price_override": "142.1234", "remark": "确认价"},
                                   expected_version=1, actor="原修改人")
        return self.exporter.export_snapshot()

    @staticmethod
    def resign(snapshot):
        snapshot["digest"] = snapshot_digest(snapshot)
        return snapshot

    def apply(self, snapshot):
        return self.receiver.apply(snapshot, actor="当前管理员", expected_digest=snapshot["digest"])

    def test_preserves_ids_versions_sources_history_references_and_manual_price(self):
        snapshot = self.seed()
        before = deepcopy(snapshot)
        preview = self.receiver.preview(snapshot)
        self.assertEqual((preview["records"], preview["revisions"], preview["items"]), (2, 3, 2))
        self.assertFalse(preview["already_imported"])
        self.assertEqual(self.target_service.list()["total"], 0)
        self.assertEqual(self.apply(snapshot), preview)
        self.assertEqual(snapshot, before)
        self.assertEqual(CostingTransferService(self.target).export_snapshot(), snapshot)
        edited = next(row for row in snapshot["records"] if row["version"] == 2)
        self.assertEqual(self.target_service.get(edited["id"])["suggested_tax_price"], "142.1234")
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT value FROM unrelated_products").fetchone(), ("existing price",))
            audit = conn.execute("SELECT action,target_type,target_key,actor FROM audit_logs").fetchone()
        self.assertEqual(audit, ("接入成本试用资料", "costing_transfer", snapshot["digest"], "当前管理员"))

    def test_legacy_derived_prices_and_absent_fields_are_not_rewritten(self):
        snapshot = self.seed()
        for record in snapshot["records"]:
            record["minimum_tax_price"] = "125"
            if record["suggested_tax_price_override"] is None:
                record.pop("suggested_tax_price_override")
                record.pop("suggested_tax_price")
        # This emulates a package created before the pricing formula changed.
        for revision in snapshot["revisions"]:
            for record in (revision["before"], revision["after"]):
                if record is not None:
                    record["minimum_tax_price"] = "125"
                    if record["suggested_tax_price_override"] is None:
                        record.pop("suggested_tax_price_override")
                        record.pop("suggested_tax_price")
        self.resign(snapshot)
        self.apply(snapshot)
        self.assertEqual(CostingTransferService(self.target).export_snapshot(), snapshot)
        for record in self.target_service.list()["records"]:
            self.assertEqual(record["minimum_tax_price"], "137.5")
        self.assertEqual(self.source_service.list()["total"], 2)

    def test_repeat_and_concurrent_apply_are_idempotent_without_duplicate_audit(self):
        snapshot = self.seed()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.apply(snapshot), range(2)))
        self.assertEqual(sorted(row["already_imported"] for row in results), [False, True])
        self.assertTrue(self.receiver.preview(snapshot)["already_imported"])
        self.assertTrue(self.apply(snapshot)["already_imported"])
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0], 1)

    def test_partial_existing_or_changed_target_is_never_overwritten(self):
        snapshot = self.seed()
        existing = self.target_service.create(self.sheet(), actor="目标原用户")
        for operation in (self.receiver.preview, self.apply):
            with self.assertRaises(CostingConflict):
                operation(snapshot)
        self.assertEqual(self.target_service.get(existing["id"]), existing)
        self.assertEqual(self.target_service.list()["total"], 1)

    def test_changed_after_transfer_cannot_be_replaced_by_old_package(self):
        snapshot = self.seed()
        self.apply(snapshot)
        original = snapshot["records"][0]
        changed = self.target_service.update(original["id"], {"remark": "正式新内容"},
                                             expected_version=original["version"], actor="正式用户")
        with self.assertRaises(CostingConflict):
            self.apply(snapshot)
        self.assertEqual(self.target_service.get(changed["id"]), changed)

    def test_changed_digest_or_unapproved_digest_is_rejected(self):
        snapshot = self.seed()
        tampered = deepcopy(snapshot)
        tampered["references"]["cartons"][0]["unit_price"] = "99"
        with self.assertRaises(CostingValidationError):
            self.apply(tampered)
        with self.assertRaises(CostingConflict):
            self.receiver.apply(snapshot, actor="管理员", expected_digest="0" * 64)
        with self.assertRaises(CostingConflict):
            self.receiver.apply(snapshot, actor="管理员", expected_digest="无效")
        self.assertEqual(self.target_service.list()["total"], 0)

    def test_invalid_shapes_and_record_metadata_are_validation_errors(self):
        snapshot = self.seed()
        invalid = []
        for key, value in (("format_version", True), ("records", {}), ("revisions", []), ("references", [])):
            item = deepcopy(snapshot)
            item[key] = value
            invalid.append(item)
        for key, value in (("id", "../path"), ("version", True), ("code", "K BAD"), ("updated_at", "yesterday"),
                           ("source", []), ("status", {}), ("net_cost", "Infinity"), ("net_cost", "3000000000000"),
                           ("issues", "wrong")):
            item = deepcopy(snapshot)
            item["records"][0][key] = value
            invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(CostingValidationError):
                self.receiver.preview(item)
        self.assertEqual(self.target_service.list()["total"], 0)

    def test_revision_relation_chain_and_last_snapshot_are_checked(self):
        snapshot = self.seed()
        for field, value in (("record_id", "f" * 32), ("version", 99), ("actor", "伪造"), ("action", []), ("before", {})):
            item = deepcopy(snapshot)
            item["revisions"][0][field] = value
            self.resign(item)
            with self.subTest(field=field), self.assertRaises(CostingValidationError):
                self.apply(item)
        item = deepcopy(snapshot)
        item["records"][0]["remark"] = "与最后修订不一致"
        self.resign(item)
        with self.assertRaises(CostingValidationError):
            self.apply(item)

    def test_editor_consumed_item_issues_and_reference_options_are_validated(self):
        snapshot = self.seed()
        for invalid_issues in ("not a list", {}, [None]):
            corrupted = deepcopy(snapshot)
            all_records = corrupted["records"] + [record for revision in corrupted["revisions"]
                                                   for record in (revision["before"], revision["after"]) if record is not None]
            for record in all_records:
                record["items"][0]["issues"] = invalid_issues
            self.resign(corrupted)
            with self.subTest(issues=invalid_issues), self.assertRaises(CostingValidationError):
                self.apply(corrupted)
        for field in ("rates", "cartons"):
            corrupted = deepcopy(snapshot)
            corrupted["references"][field] = [None]
            self.resign(corrupted)
            with self.subTest(reference=field), self.assertRaises(CostingValidationError):
                self.apply(corrupted)
        self.assertEqual(self.target_service.list()["total"], 0)

    def test_existing_null_reference_row_is_not_treated_as_empty_target(self):
        snapshot = self.seed()
        with sqlite3.connect(self.target) as conn:
            conn.execute("INSERT INTO costing_reference_data VALUES ('workbook', 'null')")
        for operation in (self.receiver.preview, self.apply):
            with self.assertRaises(CostingValidationError):
                operation(snapshot)
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT payload_json FROM costing_reference_data").fetchone()[0], "null")
        self.assertEqual(self.target_service.list()["total"], 0)
        item = deepcopy(snapshot)
        item["revisions"].pop()
        self.resign(item)
        with self.assertRaises(CostingValidationError):
            self.apply(item)

    def test_audit_failure_rolls_back_all_records_revisions_and_references(self):
        snapshot = self.seed()
        with patch.object(transfer_repository, "log_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                self.apply(snapshot)
        self.assertEqual(self.target_service.list()["total"], 0)
        with sqlite3.connect(self.target) as conn:
            for table in ("costing_records", "costing_revisions", "costing_reference_data", "audit_logs"):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_size_nonfinite_values_and_missing_actor_are_rejected(self):
        snapshot = self.seed()
        with patch.object(transfer_service, "MAX_TRANSFER_BYTES", 10):
            with self.assertRaises(CostingValidationError):
                self.apply(snapshot)
        corrupted = deepcopy(snapshot)
        corrupted["references"]["invalid"] = float("nan")
        with self.assertRaises(CostingValidationError):
            self.apply(corrupted)
        with self.assertRaises(CostingValidationError):
            self.receiver.apply(snapshot, actor=" ", expected_digest=snapshot["digest"])

    def test_reference_presence_is_preserved_and_reference_only_target_is_not_empty(self):
        self.source_service.create(self.sheet(), actor="test")
        snapshot = self.exporter.export_snapshot()
        self.assertIsNone(snapshot["references"])
        self.apply(snapshot)
        self.assertIsNone(CostingTransferService(self.target).export_snapshot()["references"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reference-only.sqlite3"
            initialize_costing_database(path)
            with sqlite3.connect(path) as conn:
                conn.execute("INSERT INTO costing_reference_data VALUES ('workbook', '{}')")
            with self.assertRaises(CostingConflict):
                CostingTransferService(path).preview(snapshot)

    def test_export_checks_index_payload_consistency_and_does_not_initialize_missing_database(self):
        self.source_service.create(self.sheet(), actor="test")
        with sqlite3.connect(self.source) as conn:
            conn.execute("UPDATE costing_records SET code='MISMATCH'")
        with self.assertRaises(CostingValidationError):
            self.exporter.export_snapshot()
        missing = Path(self.temporary.name) / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            CostingTransferService(missing).export_snapshot()
        self.assertFalse(missing.exists())

    def test_order_changes_do_not_change_digest_or_idempotence(self):
        snapshot = self.seed()
        shuffled = deepcopy(snapshot)
        shuffled["records"].reverse()
        shuffled["revisions"].reverse()
        self.assertEqual(snapshot_digest(shuffled), snapshot["digest"])
        self.apply(shuffled)
        self.assertTrue(self.apply(snapshot)["already_imported"])
        self.assertEqual(json.loads(json.dumps(snapshot)), CostingTransferService(self.target).export_snapshot())
