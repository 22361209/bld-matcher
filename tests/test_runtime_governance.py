from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.database import connect
from app.migrations import MIGRATIONS
from app.modules.admin.persistence import ensure_default_admin
from app.modules.products.sync_infrastructure import ProductPackageStore
from app.platform.retention import RuntimeRetentionService
from app.platform.runtime import RuntimeHealthService
from app.platform.runtime_config import RuntimeSettings


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "historical"


class RuntimeGovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "runtime.sqlite3"
        with connect(self.database_path):
            pass

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_product_package_extraction_rejects_traversal_and_size_expansion(self) -> None:
        cases = (
            ("traversal", "../escape.txt", b"x", {}),
            ("oversized", "data/large.bin", b"12345", {"max_member_bytes": 4}),
        )
        for label, member_name, content, limits in cases:
            with self.subTest(case=label):
                package_path = self.root / f"{label}.tar.gz"
                member = tarfile.TarInfo(member_name)
                member.size = len(content)
                with tarfile.open(package_path, "w:gz") as archive:
                    archive.addfile(member, io.BytesIO(content))
                destination = self.root / f"extract-{label}"
                destination.mkdir()
                with self.assertRaises(ValueError):
                    ProductPackageStore._safe_extract(package_path, destination, **limits)
                self.assertFalse((self.root / "escape.txt").exists())

    def test_health_and_retention_is_dry_run_first(self) -> None:
        with connect(self.database_path) as connection:
            ensure_default_admin(connection, username="health-admin", password="health-password")
        settings = RuntimeSettings(
            upload_retention_days=1,
            output_retention_days=1,
            backup_retention_days=1,
        )
        health = RuntimeHealthService(self.database_path, settings)
        missing_database = self.root / "data" / "missing.sqlite3"
        missing_health = RuntimeHealthService(missing_database, settings)
        self.assertFalse(missing_health.readiness().ready)
        self.assertFalse(missing_database.exists(), "health checks must use a read-only database connection")
        unmigrated_database = self.root / "data" / "unmigrated.sqlite3"
        sqlite3.connect(unmigrated_database).close()
        unmigrated = RuntimeHealthService(unmigrated_database, settings).readiness()
        self.assertTrue(unmigrated.checks["database"]["ok"])
        self.assertEqual(unmigrated.checks["migrations"]["reason"], "missing_or_unreadable")
        self.assertEqual(unmigrated.checks["business_probe"]["reason"], "missing_or_unreadable")
        self.assertTrue(health.readiness().ready)

        uploads = self.root / "uploads"
        outputs = self.root / "outputs"
        backups = self.root / "data" / "local-backups"
        old_upload = uploads / "old.txt"
        old_upload.parent.mkdir(parents=True)
        old_upload.write_text("old", encoding="utf-8")
        old_stamp = (datetime.now() - timedelta(days=2)).timestamp()
        os.utime(old_upload, (old_stamp, old_stamp))
        protected = outputs / "protected.xlsx"
        protected.parent.mkdir(parents=True)
        protected.write_bytes(b"protected")
        os.utime(protected, (old_stamp, old_stamp))
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO api_artifacts
                  (id, owner_id, filename, storage_path, content_type, size_bytes, sha256,
                   created_at, expires_at, last_downloaded_at)
                VALUES ('art_protected', 'key:1', 'protected.xlsx', ?, 'application/octet-stream',
                        9, 'digest', ?, ?, '')
                """,
                (
                    str(protected),
                    (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
                    (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            connection.execute(
                """
                INSERT INTO api_artifacts
                  (id, owner_id, filename, storage_path, content_type, size_bytes, sha256,
                   created_at, expires_at, last_downloaded_at)
                VALUES ('art_expired_shared', 'key:old', 'protected.xlsx', ?, 'application/octet-stream',
                        9, 'digest', ?, ?, '')
                """,
                (
                    str(protected),
                    (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
                    (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            connection.commit()
        retention = RuntimeRetentionService(
            self.database_path,
            upload_root=uploads,
            output_root=outputs,
            backup_roots=(backups,),
            settings=settings,
        )
        plan = retention.build_plan()
        self.assertIn(old_upload.resolve(), plan.files["uploads"])
        self.assertNotIn(protected.resolve(), plan.files["outputs"])
        self.assertIn("art_expired_shared", plan.artifact_ids)
        self.assertTrue(old_upload.exists(), "building a plan must be dry-run")
        retention.apply(plan, actor="runtime-test")
        self.assertFalse(old_upload.exists())
        self.assertTrue(protected.exists())
        with connect(self.database_path) as connection:
            audit = connection.execute(
                "SELECT action FROM audit_logs WHERE target_type = 'runtime_retention' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(audit["action"], "执行运行数据保留期清理")

    def test_zero_scoped_retention_keeps_inquiry_material_and_contract_files_indefinitely(self) -> None:
        settings = RuntimeSettings(
            upload_retention_days=1,
            output_retention_days=1,
            inquiry_upload_retention_days=0,
            inquiry_output_retention_days=0,
            material_upload_retention_days=0,
            material_output_retention_days=0,
            contract_output_retention_days=0,
        )
        uploads = self.root / "uploads"
        outputs = self.root / "outputs"
        old_upload = uploads / "u1-user" / "inquiry-20260729-120000-user.xlsx"
        old_output = outputs / "u1-user" / "re260729-user-inquiry.xlsx"
        old_material_upload = uploads / "u1-user" / "material-plan-20260729-120000-user.xlsx"
        old_material_output = outputs / "u1-user" / "007-七月料单.xlsx"
        old_duplicate_material_output = outputs / "u1-user" / "007-七月料单_2.xlsx"
        old_contract_output = outputs / "u1-user" / "销售合同" / "客户甲" / "SC-001客户甲.pdf"
        old_non_scoped_upload = uploads / "u1-user" / "product-data-20260729-120000-user.tar.gz"
        old_non_scoped_output = outputs / "u1-user" / "business-data-007-20260729.tar.gz"
        old_upload.parent.mkdir(parents=True)
        old_output.parent.mkdir(parents=True)
        old_contract_output.parent.mkdir(parents=True)
        old_upload.write_bytes(b"source")
        old_output.write_bytes(b"result")
        old_material_upload.write_bytes(b"material-source")
        old_material_output.write_bytes(b"material-result")
        old_duplicate_material_output.write_bytes(b"duplicate-material-result")
        old_contract_output.write_bytes(b"contract")
        old_non_scoped_upload.write_bytes(b"other-source")
        old_non_scoped_output.write_bytes(b"other-result")
        old_stamp = (datetime.now() - timedelta(days=400)).timestamp()
        for path in (
            old_upload,
            old_output,
            old_material_upload,
            old_material_output,
            old_duplicate_material_output,
            old_contract_output,
            old_non_scoped_upload,
            old_non_scoped_output,
        ):
            os.utime(path, (old_stamp, old_stamp))
        retention = RuntimeRetentionService(
            self.database_path,
            upload_root=uploads,
            output_root=outputs,
            backup_roots=(),
            settings=settings,
        )
        plan = retention.build_plan()
        self.assertNotIn(old_upload.resolve(), plan.files["uploads"])
        self.assertNotIn(old_output.resolve(), plan.files["outputs"])
        self.assertNotIn(old_material_upload.resolve(), plan.files["uploads"])
        self.assertNotIn(old_material_output.resolve(), plan.files["outputs"])
        self.assertNotIn(old_duplicate_material_output.resolve(), plan.files["outputs"])
        self.assertNotIn(old_contract_output.resolve(), plan.files["outputs"])
        self.assertIn(old_non_scoped_upload.resolve(), plan.files["uploads"])
        self.assertIn(old_non_scoped_output.resolve(), plan.files["outputs"])

    def test_historical_database_fixture_matrix_upgrades_without_data_loss(self) -> None:
        expected_migrations = {migration_id for migration_id, _migration in MIGRATIONS}
        for fixture_path in sorted(FIXTURE_ROOT.glob("*.sql")):
            with self.subTest(fixture=fixture_path.name):
                database_path = self.root / f"{fixture_path.stem}.sqlite3"
                raw = sqlite3.connect(database_path)
                try:
                    raw.executescript(fixture_path.read_text(encoding="utf-8"))
                    raw.commit()
                finally:
                    raw.close()
                with connect(database_path) as connection:
                    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                    migrations = {
                        str(row["id"]) for row in connection.execute("SELECT id FROM schema_migrations").fetchall()
                    }
                    tables = {
                        str(row["name"])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        ).fetchall()
                    }
                    self.assertEqual(integrity, "ok")
                    self.assertTrue(expected_migrations.issubset(migrations))
                    self.assertIn("background_jobs", tables)
                    self.assertNotIn("shipment_recognition_jobs", tables)
                    if fixture_path.name.startswith("v000"):
                        product = connection.execute(
                            "SELECT bld_no, price_cny, product_status FROM products WHERE bld_no = 'HIST-000'"
                        ).fetchone()
                        self.assertEqual(product["bld_no"], "HIST-000")
                    if fixture_path.name.startswith("v006"):
                        job = connection.execute(
                            "SELECT status, error_code FROM background_jobs WHERE id = 'legacy-job-006'"
                        ).fetchone()
                        self.assertEqual(job["status"], "failed")
                        self.assertEqual(job["error_code"], "job.legacy_interrupted")
                    if fixture_path.name.startswith("v012"):
                        quote = connection.execute(
                            "SELECT bld_no, version FROM quote_records WHERE bld_no = 'HIST-Q-012'"
                        ).fetchone()
                        self.assertEqual(quote["version"], 1)


if __name__ == "__main__":
    unittest.main()
