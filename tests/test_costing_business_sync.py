from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.modules.costing.business_sync import apply_costing, costing_state, export_costing, preview_costing, validate_costing
from app.modules.costing.domain import CostingValidationError
from app.modules.costing.repository import SQLiteCostingRepository, initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.transfer_service import snapshot_digest


class CostingBusinessSyncTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source.sqlite3"
        self.target = Path(temporary.name) / "target.sqlite3"
        for path in (self.source, self.target):
            initialize_costing_database(path)

    @staticmethod
    def create(path, code):
        return CostingService(path).create({"code": code, "items": [
            {"name": "测试件", "product_code": "TEST-007", "material_attribute": "外购", "amount": "23.47"}
        ]}, actor="测试人")

    @staticmethod
    def export(path):
        with closing(sqlite3.connect(path)) as connection:
            return export_costing(connection)

    def apply(self, snapshot, *, selected=None):
        with closing(sqlite3.connect(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return apply_costing(connection, snapshot, selected or set())

    def preview(self, snapshot):
        with closing(sqlite3.connect(self.target)) as connection:
            return preview_costing(connection, snapshot)

    @staticmethod
    def references(path, value):
        with closing(sqlite3.connect(path)) as connection, connection:
            SQLiteCostingRepository(connection).save_references(value)

    def test_empty_and_reference_only_packages_are_valid_and_never_delete_target_rows(self):
        original = self.create(self.target, "NAS-ONLY")
        self.assertEqual(self.preview(self.export(self.source))["records"], 0)
        self.apply(self.export(self.source))
        self.assertEqual(CostingService(self.target).get(original["id"]), original)
        refs = {"cartons": [{"code": "TEST-BOX", "unit_price": "7.50"}]}
        self.references(self.source, refs)
        package = self.export(self.source)
        self.assertEqual(self.preview(package)["counts"]["new"], 1)
        self.assertEqual(self.apply(package)["new"], 1)
        self.assertEqual(self.apply(package)["unchanged"], 1)
        self.assertEqual(self.export(self.target)["references"], refs)
        newer = {"rates": [{"name": "测试工时", "amount": "1.2"}]}
        self.references(self.source, newer)
        next_package = self.export(self.source)
        info = self.preview(next_package)["conflicts"]
        self.assertEqual([(i["key"], i["selectable"]) for i in info], [("references", True)])
        self.assertEqual(self.apply(next_package)["conflict"], 1)
        self.assertEqual(self.export(self.target)["references"], refs)
        self.assertEqual(self.apply(next_package, selected={"references"})["updated"], 1)
        self.assertEqual(self.export(self.target)["references"], newer)

    def test_numeric_revision_ids_are_local_and_full_common_history_is_required(self):
        self.create(self.target, "NAS-LOCAL")
        record = self.create(self.source, "SYNC-A")
        source = CostingService(self.source)
        initial = self.export(self.source)
        self.assertEqual(self.apply(initial)["new"], 1)
        self.assertNotEqual(source.revisions(record["id"])[0]["id"],
                            CostingService(self.target).revisions(record["id"])[0]["id"])
        self.assertEqual(self.apply(initial)["unchanged"], 1)
        edited = source.update(record["id"], {"remark": "保留原资料"}, expected_version=1, actor="修改人")
        newer = self.export(self.source)
        self.assertEqual(self.apply(newer)["updated"], 1)
        self.assertEqual(CostingService(self.target).get(record["id"]), edited)
        self.assertEqual(self.apply(initial)["stale"], 1)

        # Same latest record, but a different immutable snapshot in their shared past.
        diverged = deepcopy(dict(newer))
        first, last = sorted(diverged["revisions"], key=lambda revision: revision["version"])
        first["after"]["remark"] = last["before"]["remark"] = "另一设备不同的历史"
        diverged["digest"] = snapshot_digest(diverged)
        conflict = self.preview(diverged)["conflicts"][0]
        self.assertFalse(conflict["selectable"])
        self.assertIn("历史", conflict["reason"])
        self.assertEqual(self.apply(diverged)["conflict"], 1)
        with self.assertRaises(CostingValidationError):
            self.apply(diverged, selected={record["id"]})
        self.assertEqual(CostingService(self.target).get(record["id"]), edited)

    def test_independent_model_code_and_cross_record_item_collisions_keep_target(self):
        installed = self.create(self.target, "same")
        separate = self.create(self.source, "same")
        another = self.create(self.source, "another")
        snapshot = deepcopy(dict(self.export(self.source)))
        for item in [*snapshot["records"], *(r["after"] for r in snapshot["revisions"])]:
            if item["id"] == another["id"]:
                item["items"][0]["id"] = installed["items"][0]["id"]
        snapshot["digest"] = snapshot_digest(snapshot)
        preview = self.preview(snapshot)
        self.assertEqual({row["key"] for row in preview["conflicts"]}, {separate["id"], another["id"]})
        self.assertEqual(self.apply(snapshot)["conflict"], 2)
        self.assertEqual(self.export(self.target)["records"], [installed])

    def test_incremental_rename_cannot_overwrite_a_separate_target_model(self):
        record = self.create(self.source, "A")
        self.apply(self.export(self.source))
        self.create(self.target, "B")
        CostingService(self.source).update(record["id"], {"code": "B"}, expected_version=1, actor="源管理员")
        self.assertEqual(self.apply(self.export(self.source))["conflict"], 1)
        self.assertEqual(CostingService(self.target).get(record["id"])["code"], "A")

    def test_tampering_and_partial_writes_use_caller_rollback(self):
        self.create(self.source, "ROLLBACK")
        snapshot = self.export(self.source)
        invalid = deepcopy(dict(snapshot))
        invalid["records"][0]["calculated_cost"] = "1000"
        with self.assertRaises(CostingValidationError):
            self.apply(invalid)
        accepted = validate_costing(snapshot)
        accepted["records"][0]["name"] = "已修改"
        accepted["digest"] = snapshot_digest(accepted)
        with self.assertRaises(CostingValidationError):
            self.apply(accepted)
        with closing(sqlite3.connect(self.target)) as connection:
            original_state = costing_state(connection)
            connection.execute("""CREATE TRIGGER reject_history BEFORE INSERT ON costing_revisions
                                BEGIN SELECT RAISE(ABORT, 'audit failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.apply(self.export(self.source))
        with closing(sqlite3.connect(self.target)) as connection:
            self.assertEqual(costing_state(connection), original_state)
            self.assertEqual(export_costing(connection)["records"], [])

    def test_state_token_includes_history_and_reference_changes(self):
        self.create(self.target, "LOCAL")
        with closing(sqlite3.connect(self.target)) as connection, connection:
            original = costing_state(connection)
            connection.execute("UPDATE costing_revisions SET actor='历史发生改变'")
            changed = costing_state(connection)
            self.assertNotEqual(original, changed)
            SQLiteCostingRepository(connection).save_references({"rates": []})
            self.assertNotEqual(changed, costing_state(connection))

    def test_middle_revisions_validate_stored_amounts_and_exact_json_types(self):
        item = self.create(self.source, "INTERMEDIATE")
        CostingService(self.source).update(item["id"], {"remark": "第二版"}, expected_version=1, actor="编辑")
        snapshot = self.export(self.source)
        for invalid in ("0e-1000000", "Infinity", {"value": "23"}, True):
            with self.subTest(invalid=invalid):
                bad = deepcopy(dict(snapshot))
                first, second = bad["revisions"]
                first["after"]["items"][0]["line_total"] = second["before"]["items"][0]["line_total"] = invalid
                bad["digest"] = snapshot_digest(bad)
                with self.assertRaises(CostingValidationError):
                    self.apply(bad)
        bad = deepcopy(dict(snapshot))
        first, second = bad["revisions"]
        first["after"]["source_info"] = {"quantity": 1}
        second["before"]["source_info"] = {"quantity": True}
        bad["digest"] = snapshot_digest(bad)
        with self.assertRaises(CostingValidationError):
            self.apply(bad)

    def test_deleted_item_ids_remain_owned_by_the_original_model_history(self):
        retired = self.create(self.target, "RETIRED")
        CostingService(self.target).update(retired["id"], {"items": []}, expected_version=1, actor="编辑")
        self.create(self.source, "OTHER")
        snapshot = deepcopy(dict(self.export(self.source)))
        for record in (snapshot["records"][0], snapshot["revisions"][0]["after"]):
            record["items"][0]["id"] = retired["items"][0]["id"]
        snapshot["digest"] = snapshot_digest(snapshot)
        self.assertEqual(self.apply(snapshot)["conflict"], 1)

    def test_historical_amounts_sources_attributes_and_missing_fields_survive_round_trip(self):
        self.create(self.source, "HIST")
        snapshot = deepcopy(dict(self.export(self.source)))
        for sheet in (snapshot["records"][0], snapshot["revisions"][0]["after"]):
            sheet["items"][0]["line_total"] = "25.6000"
            sheet["items"][0]["source_info"] = {"cell": "C41"}
            sheet.update(known_subtotal="25.6000", calculated_cost="25.6000", net_cost="25.6000",
                         effective_cost="25.6000", minimum_tax_price="30")
            del sheet["suggested_tax_price"]
        snapshot["digest"] = snapshot_digest(snapshot)
        self.apply(snapshot)
        returned = self.export(self.target)
        self.assertEqual(returned["records"], snapshot["records"])
        self.assertEqual(returned["revisions"], snapshot["revisions"])
