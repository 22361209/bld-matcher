from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from tools.quick_release import ReleaseError, baseline, pack, prepare


class QuickReleaseTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.src = self.work / "source"
        self.src.mkdir()
        for name in ("app.py", "wsgi.py", "app/config.py", "app/platform/permissions.py", "requirements.txt",
                     "app/modules/costing/service.py", "templates/main.html", "tools/migrate_product_images.py",
                     "docs/api/product.md", "项目交接说明.md"):
            self.write(name, "old")
        self.base = self.work / "baseline.json"
        baseline(self.src, self.base)
        self.release = self.work / "release"

    def write(self, name, value):
        path = self.src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def fragment(self, data="none", operations="restart-required"):
        self.write("changes/fix.json", json.dumps({"impact": {"data": data, "operations": operations, "api": "compatible"}}))

    def test_compatible_snapshot_and_two_successive_quick_updates(self):
        self.write("app/modules/costing/service.py", "new formula")
        (self.src / "templates/main.html").unlink()
        self.fragment()
        revision = "a" * 40 + "-" + "b" * 12
        prepare(self.src, self.base, self.release, revision)
        self.assertEqual((self.release / "app/modules/costing/service.py").read_text(), "new formula")
        self.assertFalse((self.release / "templates/main.html").exists())
        self.assertFalse((self.release / "tools").exists())
        self.assertEqual((self.release / "docs/api/product.md").read_text(), "old")
        self.assertEqual((self.release / "项目交接说明.md").read_text(), "old")
        self.assertEqual((self.release / ".deployment-version").read_text().strip(), revision)
        self.assertEqual(os.readlink(self.release / "data"), "/app/data")

        previous = self.release / ".quick-baseline.json"
        self.write("app/modules/costing/service.py", "newer formula")
        second = self.work / "second"
        with self.assertRaisesRegex(ReleaseError, "缺少新增或更新"):
            prepare(self.src, self.base, second, revision, previous)
        self.assertFalse(second.exists())
        # Returning to the exact base is a valid rollback even if a previous fragment is removed.
        self.write("app/modules/costing/service.py", "old")
        self.write("templates/main.html", "old")
        (self.src / "changes/fix.json").unlink()
        prepare(self.src, self.base, second, "a" * 40, previous)

    def test_sensitive_changes_and_paths_fail_before_publication(self):
        self.fragment()
        for name in ("app/modules/costing/schema.py", "app/platform/permissions.py", "tools/migrate_product_images.py",
                     "requirements.txt", "static/product_images/secret.jpg", ".env", "uploads/private.txt"):
            with self.subTest(path=name):
                path = self.src / name
                old = path.read_bytes() if path.exists() else None
                self.write(name, "modified")
                with self.assertRaises(ReleaseError):
                    prepare(self.src, self.base, self.release, "1234567")
                self.assertFalse(self.release.exists())
                if old is None:
                    path.unlink()
                else:
                    path.write_bytes(old)

        (self.src / "static/product_images").rmdir()
        (self.src / "templates/private.html").symlink_to(self.base)
        with self.assertRaisesRegex(ReleaseError, "只允许普通文件"):
            prepare(self.src, self.base, self.release, "1234567")
        self.assertFalse(self.release.exists())

    def test_new_asset_type_preserves_full_build_and_disables_quick_release(self):
        self.write("app/modules/costing/extra.bin", "a future resource")
        baseline(self.src, self.base)
        self.assertIn("quick_disabled", json.loads(self.base.read_text()))
        (self.src / "app/modules/costing/extra.bin").unlink()
        with self.assertRaisesRegex(ReleaseError, "此镜像仅支持完整更新"):
            prepare(self.src, self.base, self.release, "1234567")

    def test_data_and_operations_impact_guard(self):
        for data, ops in (("migration", "restart-required"), ("none", "rebuild")):
            with self.subTest(data=data, ops=ops):
                self.fragment(data, ops)
                with self.assertRaisesRegex(ReleaseError, "未声明可快速发布"):
                    prepare(self.src, self.base, self.release, "1234567")

    def test_pack_excludes_ignored_secrets_deterministically_and_rejects_tracked_secrets(self):
        self.write(".gitignore", ".env\ndata/\n")
        self.write(".env", "secret")
        self.write("data/private.db", "database")
        self.write("templates/pending.html", "uncommitted")
        subprocess.run(["git", "init", "-q", str(self.src)], check=True)
        first, second = self.work / "first.tar", self.work / "second.tar"
        pack(self.src, first)
        os.utime(self.src / "templates/pending.html", (123, 123))
        pack(self.src, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as tar:
            names = tar.getnames()
            self.assertIn("templates/pending.html", names)
            self.assertNotIn(".env", names)
            self.assertNotIn("data/private.db", names)
        (self.src / "templates/leak.html").symlink_to(self.src / ".env")
        with self.assertRaisesRegex(ReleaseError, "符号链接"):
            pack(self.src, second)
        subprocess.run(["git", "-C", str(self.src), "add", "-f", ".env"], check=True)
        with self.assertRaisesRegex(ReleaseError, "敏感路径"):
            pack(self.src, second)
