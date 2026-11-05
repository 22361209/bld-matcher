from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.modules.costing.domain import CostingConflict, CostingValidationError, calculate_cost
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.transfer_service import CostingTransferService, snapshot_digest


class CostingMetadataEditsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source, destination = (Path(temporary.name) / name for name in ("trial.sqlite3", "formal.sqlite3"))
        for path in (source, destination):
            initialize_costing_database(path)
        CostingService(source).create({"code": "HISTORY", "items": [
            {"name": "", "amount": "2", "quantity": "12", "product_code": "A",
             "material_attribute": "外购", "review_note": "核对原表", "source_info": {"row": 29}},
            {"name": "费用", "amount": "4"},
        ]}, source="excel", actor="导入员")
        package = CostingTransferService(source).export_snapshot()
        self.before = before = package["records"][0]
        before["items"][0].update(line_total="17.0000", product_code=" A ", mode=" fixed ")
        before.update(calculated_cost="21.0000", known_subtotal="21.0000", effective_cost="21.0000",
                      net_cost="21.0000", minimum_tax_price="23.10")
        del before["suggested_tax_price"]
        del before["suggested_tax_price_override"]
        package["revisions"][0]["after"] = deepcopy(before)
        package["digest"] = snapshot_digest(package)
        self.transfer = CostingTransferService(destination)
        self.transfer.apply(package, expected_digest=package["digest"], actor="交接")
        self.service = CostingService(destination)

    def test_metadata_save_preserves_original_amounts_and_audit_snapshot(self):
        before = self.before
        first, second = deepcopy(before["items"])
        for key in ("product_code", "material_attribute", "quantity"):
            del first[key]  # Older client, with only active numeric fields.
        first.update(name="新名称", review_note="", supplier="供方", amount="2.000", line_total="999",
                     issues=["客户端伪造提示"], source_info={"row": 999})
        changes = {"items": [first, second], "remark": "仅更新资料", "net_cost": "999", "status": "draft",
                   "issues": ["客户端伪造提示"], "suggested_tax_price_override": ""}
        saved = self.service.update(before["id"], changes, expected_version=1, actor="编辑员")
        self.assertEqual(saved, self.service.get(before["id"]))
        audit = self.service.revisions(before["id"])[0]
        self.assertEqual(audit["before"], before)
        self.assertEqual(audit["after"], {
            **before, "remark": "仅更新资料", "version": 2, "updated_by": "编辑员", "updated_at": saved["updated_at"],
            "items": [{**before["items"][0], "name": "新名称", "review_note": "", "supplier": "供方", "issues": []}, second],
            "issues": [], "status": "ready",
        })
        self.assertEqual(self.transfer.export_snapshot()["records"], [audit["after"]])

    def test_actual_inputs_product_code_and_row_structure_recalculate(self):
        before = self.before
        first, second = before["items"]
        for changes, expected in [
            ({"items": [{**first, "amount": "3"}, second]}, "7"),
            ({"items": [{**first, "product_code": ""}, second]}, "6"),
            ({"items": [{**first, "mode": "adjustment"}, second]}, "6"),
            ({"items": [{**first, "id": ""}, second]}, "6"),
            ({"items": [second, first]}, "6"),
            ({"items": [second]}, "4"),
            ({"items": [first, second, {"name": "新增", "amount": "1"}]}, "7"),
            ({"approved_cost": "21"}, "6"),
            ({"scrap_credit": "1"}, "6"),
            ({"theoretical_scrap": "1"}, "6"),
            ({"suggested_tax_price_override": "0"}, "6"),
        ]:
            with self.subTest(changes=changes):
                result = calculate_cost({**before, **changes}, previous=before)
                self.assertEqual(result["calculated_cost"], expected)

    def test_supplier_can_be_set_copied_and_cleared_while_old_clients_can_omit_it(self):
        first, second = deepcopy(self.before["items"])
        first["supplier"] = "  外协厂  "
        changes = {"items": [first, second]}
        record_id = self.before["id"]
        self.service.update(record_id, changes, expected_version=1, actor="采购")
        saved = self.service.get(record_id)
        self.assertEqual(saved["items"][0]["supplier"], "外协厂")
        copied = self.service.create({**self.service.calculate(saved), "code": "COPY"}, source="copy", actor="采购")
        self.assertEqual(self.service.get(copied["id"])["items"][0]["supplier"], "外协厂")

        del first["supplier"]
        first["category"] = "机加工"
        kept = self.service.update(record_id, changes, expected_version=2, actor="编辑员")
        self.assertEqual(kept["items"][0]["supplier"], "外协厂")
        first["supplier"] = ""
        self.service.update(record_id, changes, expected_version=3, actor="采购")
        cleared = self.service.get(record_id)
        self.assertEqual(cleared["items"][0]["supplier"], "")
        for record in (saved, kept, cleared):
            self.assertEqual(record["items"][0]["line_total"], "17.0000")
            self.assertEqual(record["calculated_cost"], "21.0000")

    def test_legacy_padded_mode_and_code_match_the_normalized_editor(self):
        before = self.before
        first, second = before["items"]
        for sent in ({**first, "mode": "fixed"}, {key: value for key, value in first.items() if key != "amount"}):
            with self.subTest(mode=sent["mode"]):
                sent.update(category="组装", product_code="A")
                result = calculate_cost({**before, "items": [sent, second]}, previous=before)
                self.assertEqual(result["items"][0], {**first, "category": "组装"})
                self.assertEqual(result["net_cost"], before["net_cost"])
        for clearing in ({**first, "amount": ""}, {k: v for k, v in {**first, "mode": "fixed"}.items() if k != "amount"}):
            with self.subTest(clearing=clearing):
                result = calculate_cost({**before, "items": [clearing, second]}, previous=before)
                self.assertIsNone(result["items"][0]["line_total"])
                self.assertIsNone(result["calculated_cost"])

    def test_metadata_is_validated_and_stale_updates_are_rejected(self):
        before = self.before
        first, second = before["items"]
        for invalid in ({"name": 32}, {"remark": "x" * 2001}, {"material_attribute": "采购"},
                        {"quantity": "不是数字"}):
            with self.subTest(invalid=invalid), self.assertRaises(CostingValidationError):
                self.service.update(before["id"], {"items": [{**first, **invalid}, second]}, expected_version=1, actor="工艺")
        self.assertEqual(self.service.get(before["id"])["version"], 1)
        self.service.update(before["id"], {"remark": "在先"}, expected_version=1, actor="先")
        with self.assertRaises(CostingConflict):
            self.service.update(before["id"], {"remark": "在后"}, expected_version=1, actor="后")

    def test_editing_notes_keeps_unrelated_and_identically_worded_numeric_warnings(self):
        raw = {"code": "Q", "items": [{"id": "1", "name": "", "review_note": "请填写金额"}]}
        before = calculate_cost(raw)
        before["issues"].append("原表合计需复核")
        first = {**before["items"][0], "name": "补名称", "review_note": ""}
        after = calculate_cost({**before, "items": [first]}, previous=before)
        self.assertEqual(after["items"][0]["issues"], ["请填写金额"])
        self.assertEqual(after["issues"], ["第 1 项：请填写金额", "原表合计需复核"])
        self.assertEqual(after["status"], "draft")
        first.update(name="", review_note="再次核对", amount=None)
        latest = calculate_cost({**after, "items": [first]}, previous=after)
        self.assertEqual(latest["items"][0]["issues"], ["请填写金额", "请填写成本项名称", "再次核对"])
        self.assertCountEqual(latest["issues"], ["原表合计需复核"] + ["第 1 项：" + note for note in latest["items"][0]["issues"]])
