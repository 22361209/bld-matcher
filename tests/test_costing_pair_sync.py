from contextlib import closing
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

from app.modules.business_sync.infrastructure import BusinessSyncRepository
from app.modules.costing.business_sync import apply_costing, export_costing, preview_costing, validate_costing
from app.modules.costing.domain import CostingValidationError
from app.modules.costing.pair_catalog import CostingPairCatalog
from app.modules.costing.repository import initialize_costing_database
from app.modules.costing.service import CostingService
from app.modules.costing.transfer_service import CostingTransferService, snapshot_digest


class PairedCostTransferTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source, self.target = self.root / "src.sqlite3", self.root / "dst.sqlite3"
        for path in (self.source, self.target):
            initialize_costing_database(path)
        self.costs = CostingService(self.source)

    def pair(self):
        return self.costs.create({
            "code": "K8301LA02-6", "create_pair": True,
            "items": [{"name": "胶套", "product_code": "C8043", "amount": "4.35", "supplier": "甲厂"},
                      {"name": "支架", "product_code": "K2101LA", "amount": "7", "pair_kind": "handed"},
                      {"name": "单侧件", "amount": "1", "pair_kind": "local"}],
        }, actor="配对建档")

    @staticmethod
    def snapshot(path):
        with closing(sqlite3.connect(path)) as conn:
            return export_costing(conn)

    def preview(self, snapshot):
        with closing(sqlite3.connect(self.target)) as conn:
            return preview_costing(conn, snapshot)

    def apply(self, snapshot):
        with closing(sqlite3.connect(self.target)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return apply_costing(conn, snapshot, set())

    def test_standalone_pair_round_trip_preserves_working_links_and_cross_device_updates(self):
        left = self.pair()
        self.costs.update(left["id"], {"peer_version": 1, "remark": "核对"}, expected_version=1, actor="源端")
        snapshot = CostingTransferService(self.source).export_snapshot()
        self.assertEqual(snapshot["format_version"], 2)
        transfer = CostingTransferService(self.target)
        transfer.apply(snapshot, expected_digest=snapshot["digest"], actor="接收端")
        self.assertEqual(transfer.export_snapshot(), snapshot)
        target = CostingService(self.target)
        right = target.get(left["pair"]["peer_id"])
        self.assertEqual(right["items"][0]["supplier"], "甲厂")
        right["items"][0].update(amount="5", product_code="C8043A", supplier="乙厂")
        target.update(right["id"], right, expected_version=2, actor="接收端")
        updated = target.get(left["id"])
        self.assertEqual(updated["items"][0]["supplier"], "甲厂")
        self.assertEqual((updated["items"][0]["product_code"], updated["items"][0]["amount"]), ("C8043A", "5"))
        self.assertEqual(len(updated["items"]), 3)
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self.assertEqual(apply_costing(conn, self.snapshot(self.target), set())["updated"], 2)
        self.assertEqual(self.costs.get(left["id"]), updated)
        self.assertEqual(self.costs.get(right["id"])["items"][0]["supplier"], "乙厂")
        self.assertEqual(self.apply(snapshot)["stale"], 2)

    def test_both_sheets_are_kept_together_when_only_peer_code_collides(self):
        self.pair()
        local = CostingService(self.target).create({"code": "K8301RA02-6", "remark": "独立历史"}, actor="本机")
        single = self.costs.create({"code": "K9000"}, actor="源端")
        snapshot = self.snapshot(self.source)
        preview = self.preview(snapshot)
        self.assertEqual(preview["pairs"], 1)
        self.assertEqual([(row["label"], row["peer_code"]) for row in preview["conflicts"]], [
            ("K8301LA02-6", "K8301RA02-6"), ("K8301RA02-6", "K8301LA02-6"),
        ])
        self.assertTrue(all(not row["selectable"] for row in preview["conflicts"]))
        self.assertEqual(self.apply(snapshot)["conflict"], 2)
        self.assertEqual({row["id"] for row in self.snapshot(self.target)["records"]}, {single["id"], local["id"]})

    def test_old_independent_package_cannot_split_an_established_pair(self):
        left = self.costs.create({"code": "K8301LA", "items": [{"name": "主板", "amount": "1"}]}, actor="源端")
        old = self.snapshot(self.source)
        self.apply(old)
        other = CostingService(self.target)
        CostingPairCatalog(other).mirror_selected([{"id": left["id"], "version": 1}], actor="本机")
        linked = self.snapshot(self.target)
        self.assertEqual(self.apply(old)["stale"], 1)
        self.costs.update(left["id"], {"remark": "独立修改"}, expected_version=1, actor="源端")
        self.assertEqual(self.apply(self.snapshot(self.source))["conflict"], 1)
        self.assertEqual(self.snapshot(self.target), linked)

    def test_valid_legacy_prefix_can_be_followed_by_pairing_and_new_mirror(self):
        left = self.costs.create({"code": "K8301LA", "items": [{"name": "胶套", "amount": "1"}]}, actor="源端")
        self.apply(self.snapshot(self.source))
        CostingPairCatalog(self.costs).mirror_selected([{"id": left["id"], "version": 1}], actor="源端")
        snapshot = self.snapshot(self.source)
        states = self.apply(snapshot)
        self.assertEqual((states["new"], states["updated"]), (1, 1))
        self.assertEqual(self.snapshot(self.target)["records"], snapshot["records"])
        self.assertEqual(self.apply(snapshot)["unchanged"], 2)

    def test_missing_peer_revision_forged_shared_code_or_row_link_rejected_even_with_valid_checksum(self):
        left = self.pair()
        self.costs.update(left["id"], {"peer_version": 1, "remark": "第二版"}, expected_version=1, actor="源端")
        source = self.snapshot(self.source)
        peer_id = left["pair"]["peer_id"]
        for fault in ("legacy", "missing_peer", "missing_paired_save", "broken_row", "different_shared_code"):
            with self.subTest(fault=fault):
                snap = deepcopy(dict(source))
                if fault == "legacy":
                    snap["format_version"] = 1
                elif fault == "missing_peer":
                    snap["records"] = [row for row in snap["records"] if row["id"] != peer_id]
                    snap["revisions"] = [row for row in snap["revisions"] if row["record_id"] != peer_id]
                elif fault == "missing_paired_save":
                    snap["revisions"] = [row for row in snap["revisions"] if (row["record_id"], row["version"]) != (peer_id, 2)]
                    prior = next(row["after"] for row in snap["revisions"] if row["record_id"] == peer_id)
                    snap["records"] = [prior if row["id"] == peer_id else row for row in snap["records"]]
                else:
                    first = next(row for row in snap["revisions"] if (row["record_id"], row["version"]) == (peer_id, 1))
                    second = next(row for row in snap["revisions"] if (row["record_id"], row["version"]) == (peer_id, 2))
                    for sheet in (first["after"], second["before"]):
                        item = sheet["items"][0]
                        if fault == "broken_row":
                            item["peer_item_id"] = "0" * 32
                        else:
                            item["product_code"] = "错误的共用编码"
                snap["digest"] = snapshot_digest(snap)
                with self.assertRaises(CostingValidationError):
                    validate_costing(snap)

    def test_business_archive_rejects_pair_snapshot_in_old_manifest(self):
        # Use separate main databases, so the business-package backup/audit also run.
        path = self.root / "business.tar.gz"
        source_path, target_path = self.root / "source-main.sqlite3", self.root / "target-main.sqlite3"
        from app.database import connect
        for db in (source_path, target_path):
            with connect(db):
                pass
        source = BusinessSyncRepository(source_path, allow_costing=True)
        target = BusinessSyncRepository(target_path, allow_costing=True)
        self.costs = CostingService(source_path)
        self.pair()
        source.export(output_path=path, selected=("costing",), actor="源端")
        with tarfile.open(path) as tar:
            files = {member.name: json.load(tar.extractfile(member)) for member in tar}
        self.assertEqual((files["manifest.json"]["version"], files["costing.json"]["format_version"]), (7, 2))
        result = target.preview(path)
        self.assertEqual(target.apply(path, backup_path=self.root / "backup.sqlite3", expected_token=result["token"],
                                      actor="接收端", selected_conflicts={})["costing"]["new"], 2)
        files["manifest.json"]["version"] = 6
        with tarfile.open(path, "w:gz") as tar:
            for filename, value in files.items():
                content = json.dumps(value).encode()
                member = tarfile.TarInfo(filename)
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "版本"):
            target.preview(path)
