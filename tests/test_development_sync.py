from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.database import connect
from app.modules.business_sync import _database_apply
from app.modules.business_sync._development import export_aggregates
from app.modules.business_sync._development_files import MEMBER_PREFIX
from app.modules.business_sync.infrastructure import BusinessSyncRepository


PDF = b"%PDF-1.4\nsynthetic drawing\n%%EOF\n"
NOW = "2026-10-02 08:00:00"


class DevelopmentSyncTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite3"
        self.target = self.root / "target.sqlite3"
        self.source_files = self.root / "source-files"
        self.target_files = self.root / "target-files"
        for database in (self.source, self.target):
            with connect(database):
                pass
        self.src = BusinessSyncRepository(self.source, development_file_dir=self.source_files)
        self.dst = BusinessSyncRepository(self.target, development_file_dir=self.target_files)
        self.ids = {key: str(uuid4()) for key in ("project", "left", "right", "task", "root", "leftpin", "rightpin", "document", "revision", "link", "operation", "source")}
        self.seed()

    def tearDown(self):
        self.temporary.cleanup()

    def seed(self):
        i = self.ids
        with connect(self.source) as connection:
            connection.execute("INSERT INTO development_projects(id,core_number,title,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?)", (i["project"], "8461", "8461控制臂", "test", NOW, NOW))
            for side in ("left", "right"):
                connection.execute("INSERT INTO development_branches(id,project_id,code,side,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (i[side], i["project"], "K8461LA" if side == "left" else "K8461RA", side, "test", NOW, NOW))
            connection.execute("INSERT INTO development_tasks(id,project_id,name,development_mode,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (i["task"], i["project"], "球销", "borrowed", "test", NOW, NOW))
            connection.execute("INSERT INTO development_sources(id,code,name,created_by,created_at) VALUES(?,?,?,?,?)", (i["source"], "QD8070", "8070球销", "test", NOW))
            for side in ("left", "right"):
                connection.execute("INSERT INTO development_components(id,project_id,branch_id,task_id,code,borrowed_code,borrowed_source_id,name,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (i[side + "pin"], i["project"], i[side], i["task"], "QD8461", "QD8070", i["source"], "球销", "test", NOW, NOW))
            connection.execute("INSERT INTO development_operations(id,project_id,task_id,sequence,name,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (i["operation"], i["project"], i["task"], 1, "试制", "test", NOW, NOW))
            connection.execute("INSERT INTO development_documents(id,project_id,kind,number,title,created_by,created_at) VALUES(?,?,?,?,?,?,?)", (i["document"], i["project"], "drawing", "QD8461", "球销图", "test", NOW))
            digest = hashlib.sha256(PDF).hexdigest()
            relative = f"{i['project']}/{i['revision']}/{digest}.pdf"
            connection.execute("INSERT INTO development_document_revisions(id,document_id,project_id,revision_no,label,change_note,state,original_name,extension,content_type,size_bytes,sha256,storage_path,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (i["revision"], i["document"], i["project"], 1, "A", "首版", "released", "球销.pdf", ".pdf", "application/pdf", len(PDF), digest, relative, "test", NOW))
            connection.execute("INSERT INTO development_document_links(id,project_id,target_type,target_id,document_id,revision_id,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?)", (i["link"], i["project"], "task", i["task"], i["document"], i["revision"], "test", NOW))
            connection.commit()
        self.relative = relative
        target = self.source_files / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(PDF)

    def package(self):
        return self.src.export(output_path=self.root / "development.tar.gz", selected=("development",), actor="test")

    def apply(self, package, *, selected=False, token=None):
        preview = self.dst.preview(package)
        return self.dst.apply(package, backup_path=self.root / f"backup-{uuid4()}.sqlite3", actor="test", expected_token=token or preview["token"], selected_conflicts={"development": {self.ids["project"]}} if selected else {})

    def rewrite(self, package, transform, *, omit_file=False, version=None):
        with tarfile.open(package, "r:gz") as archive:
            members = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
        payload = json.loads(members["data.json"])
        transform(payload)
        members["data.json"] = json.dumps(payload).encode()
        if version:
            manifest = json.loads(members["manifest.json"])
            manifest["version"] = version
            members["manifest.json"] = json.dumps(manifest).encode()
        if omit_file:
            members.pop(MEMBER_PREFIX + self.relative)
        changed = self.root / f"changed-{uuid4()}.tar.gz"
        with tarfile.open(changed, "w:gz") as archive:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return changed

    def test_v5_roundtrip_preserves_shared_uuid_borrowing_and_fixed_revision(self):
        package = self.package()
        manifest, _ = self.dst.read(package)
        self.assertEqual(manifest["version"], 5)
        result = self.apply(package)
        self.assertEqual(result["development"]["new"], 1)
        with connect(self.target) as connection:
            aggregate = export_aggregates(connection)[0]
            self.assertEqual({row["task_id"] for row in aggregate["components"]}, {self.ids["task"]})
            self.assertEqual({(row["code"], row["borrowed_code"]) for row in aggregate["components"]}, {("QD8461", "QD8070")})
            self.assertEqual(aggregate["links"][0]["revision_id"], self.ids["revision"])
            self.assertEqual({row["borrowed_source_id"] for row in aggregate["components"]}, {self.ids["source"]})
            self.assertEqual(aggregate["sources"][0]["code"], "QD8070")
            self.assertEqual(connection.execute("SELECT count(*) FROM audit_logs WHERE action='导入业务数据包'").fetchone()[0], 1)
        self.assertEqual((self.target_files / self.relative).read_bytes(), PDF)

    def test_repeated_package_is_idempotent(self):
        package = self.package()
        self.apply(package)
        result = self.apply(package)
        self.assertEqual(result["development"]["unchanged"], 1)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM development_document_revisions").fetchone()[0], 1)

    def test_newer_project_is_whole_project_conflict_and_skip_copies_no_files(self):
        package = self.package()
        self.apply(package)
        changed = self.rewrite(package, lambda data: data["development"][0]["project"].update(title="包内更新", updated_at="2099-01-01 00:00:00"))
        (self.target_files / self.relative).unlink()
        preview = self.dst.preview(changed)
        self.assertEqual(preview["summary"]["development"]["counts"]["conflict"], 1)
        result = self.apply(changed)
        self.assertEqual(result["development"]["conflict"], 1)
        self.assertFalse((self.target_files / self.relative).exists())
        self.apply(changed, selected=True)
        self.assertTrue((self.target_files / self.relative).is_file())

    def test_preview_token_detects_real_local_file_content_change(self):
        package = self.package()
        self.apply(package)
        token = self.dst.preview(package)["token"]
        (self.target_files / self.relative).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "变化"):
            self.dst.apply(package, backup_path=self.root / "stale.sqlite3", actor="test", expected_token=token, selected_conflicts={})

    def test_same_revision_changed_metadata_is_rejected_even_when_confirmed(self):
        package = self.package()
        self.apply(package)
        changed = self.rewrite(package, lambda data: data["development"][0]["revisions"][0].update(label="tamper"))
        with self.assertRaisesRegex(ValueError, "不可变"):
            self.apply(changed, selected=True)

    def test_missing_revision_file_is_rejected(self):
        changed = self.rewrite(self.package(), lambda data: None, omit_file=True)
        with self.assertRaisesRegex(ValueError, "文件缺失"):
            self.dst.preview(changed)

    def test_modified_package_file_bytes_are_rejected(self):
        package = self.package()
        changed = self.rewrite(package, lambda data: data["development"][0]["revisions"][0].update(size_bytes=1))
        with self.assertRaisesRegex(ValueError, "大小"):
            self.dst.preview(changed)

    def test_component_cycle_is_rejected(self):
        changed = self.rewrite(self.package(), lambda data: data["development"][0]["components"][0].update(parent_id=data["development"][0]["components"][0]["id"]))
        with self.assertRaisesRegex(ValueError, "循环"):
            self.dst.preview(changed)

    def test_missing_shared_task_dependency_is_rejected(self):
        changed = self.rewrite(self.package(), lambda data: data["development"][0].update(tasks=[]))
        with self.assertRaisesRegex(ValueError, "引用缺失"):
            self.dst.preview(changed)

    def test_document_link_target_other_project_is_rejected(self):
        changed = self.rewrite(self.package(), lambda data: data["development"][0]["links"][0].update(target_id=str(uuid4())))
        with self.assertRaisesRegex(ValueError, "归属"):
            self.dst.preview(changed)

    def test_failure_compensates_only_new_files_and_rolls_back_rows(self):
        package = self.package()
        with patch.object(_database_apply, "log_event", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                self.apply(package)
        self.assertFalse((self.target_files / self.relative).exists())
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM development_projects").fetchone()[0], 0)
        self.assertTrue(list(self.root.glob("backup-*.sqlite3")))

    def test_final_digest_recheck_rolls_back_file_and_rows(self):
        package = self.package()
        original = _database_apply.log_event
        def mutate_file(*args, **kwargs):
            original(*args, **kwargs)
            (self.target_files / self.relative).write_bytes(b"external content change")
        with patch.object(_database_apply, "log_event", side_effect=mutate_file):
            with self.assertRaisesRegex(ValueError, "提交前"):
                self.apply(package)
        self.assertFalse((self.target_files / self.relative).exists())
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM development_projects").fetchone()[0], 0)

    def test_absent_rows_do_not_delete_local_history(self):
        package = self.package()
        self.apply(package)
        changed = self.rewrite(package, lambda data: data["development"][0].update(operations=[]))
        self.apply(changed, selected=True)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM development_operations").fetchone()[0], 1)

    def test_development_is_not_accepted_in_v4(self):
        changed = self.rewrite(self.package(), lambda data: None, version=4)
        with self.assertRaisesRegex(ValueError, "旧版"):
            self.dst.preview(changed)

    def test_uuid_cannot_be_stolen_from_another_project(self):
        package = self.package()
        self.apply(package)
        new_project = str(uuid4())
        def change_owner(data):
            aggregate = data["development"][0]
            aggregate["id"] = new_project
            aggregate["project"]["id"] = new_project
            for group in ("branches", "components", "tasks", "operations"):
                for row in aggregate[group]:
                    row["project_id"] = new_project
            aggregate.update(documents=[], revisions=[], links=[])
        changed = self.rewrite(package, change_owner, omit_file=True)
        with self.assertRaisesRegex(ValueError, "另一开发单"):
            self.dst.preview(changed)

    def test_supplier_update_requires_accepting_every_referencing_project(self):
        supplier_id, second_project, second_task = (str(uuid4()) for _ in range(3))
        with connect(self.source) as connection:
            connection.execute("INSERT INTO development_suppliers(id,name,created_by,created_at,updated_at) VALUES(?,?,?,?,?)", (supplier_id, "供应商A", "test", NOW, NOW))
            connection.execute("UPDATE development_tasks SET supplier_id=?", (supplier_id,))
            connection.execute("INSERT INTO development_projects(id,core_number,title,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?)", (second_project, "8070", "另一个开发单", "test", NOW, NOW))
            connection.execute("INSERT INTO development_tasks(id,project_id,name,supplier_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (second_task, second_project, "衬套", supplier_id, "test", NOW, NOW))
            connection.commit()
        self.apply(self.package())
        with connect(self.source) as connection:
            connection.execute("UPDATE development_suppliers SET name='供应商新名称',version=version+1")
            connection.commit()
        package = self.package()
        preview = self.dst.preview(package)
        self.assertEqual(preview["summary"]["development"]["counts"]["conflict"], 2)
        with self.assertRaisesRegex(ValueError, "所有引用"):
            self.apply(package, selected=True)
        self.dst.apply(package, backup_path=self.root / "supplier-backup.sqlite3", actor="test", expected_token=preview["token"], selected_conflicts={"development": {self.ids["project"], second_project}})
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT name FROM development_suppliers WHERE id=?", (supplier_id,)).fetchone()[0], "供应商新名称")
            self.assertEqual({row[0] for row in connection.execute("SELECT version FROM development_projects")}, {2})
        self.assertEqual(self.apply(package)["development"]["unchanged"], 2)

    def test_import_permission_is_rechecked_on_parsed_payload(self):
        package = self.package()
        token = self.dst.preview(package)["token"]
        restricted = BusinessSyncRepository(self.target, development_file_dir=self.target_files, allow_development=False)
        with self.assertRaisesRegex(ValueError, "访问权限"):
            restricted.preview(package)
        with self.assertRaisesRegex(ValueError, "访问权限"):
            restricted.apply(package, backup_path=self.root / "denied.sqlite3", actor="test", expected_token=token, selected_conflicts={})
        self.assertFalse((self.root / "denied.sqlite3").exists())
        self.assertFalse(self.target_files.exists())

    def test_confirmed_branch_without_fixed_released_drawing_is_rejected(self):
        def forge_completion(data):
            aggregate = data["development"][0]
            aggregate["branches"][0].update(owner_username="test", confirmed_by="test", confirmed_at=NOW, actual_date="2026-10-02", production_ready=1)
            aggregate["tasks"][0].update(status="completed", owner_username="test", actual_date="2026-10-02")
            aggregate["operations"][0].update(status="completed", owner_username="test", actual_date="2026-10-02")
        changed = self.rewrite(self.package(), forge_completion)
        with self.assertRaisesRegex(ValueError, "固定采用"):
            self.dst.preview(changed)

    def test_original_archive_changed_before_commit_is_rejected(self):
        package = self.package()
        original = _database_apply.log_event
        def mutate_package(*args, **kwargs):
            original(*args, **kwargs)
            package.write_bytes(b"concurrent replacement")
        with patch.object(_database_apply, "log_event", side_effect=mutate_package):
            with self.assertRaisesRegex(ValueError, "数据包已变化"):
                self.apply(package)
        self.assertFalse((self.target_files / self.relative).exists())
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM development_projects").fetchone()[0], 0)

    def test_accepted_conflict_retains_existing_omitted_revision_history(self):
        package = self.package()
        self.apply(package)
        def omit_history(data):
            aggregate = data["development"][0]
            aggregate["project"]["title"] = "只更新标题"
            aggregate.update(documents=[], revisions=[], links=[])
        changed = self.rewrite(package, omit_history, omit_file=True)
        self.apply(changed, selected=True)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM development_document_revisions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM development_document_links").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT title FROM development_projects").fetchone()[0], "只更新标题")
        self.assertEqual((self.target_files / self.relative).read_bytes(), PDF)

    def test_same_borrowed_source_uuid_cannot_change_immutable_identity(self):
        package = self.package()
        self.apply(package)
        changed = self.rewrite(package, lambda data: data["development"][0]["sources"][0].update(name="被改名的源"))
        with self.assertRaisesRegex(ValueError, "不可变来源"):
            self.dst.preview(changed)
        with connect(self.target) as connection:
            self.assertEqual(connection.execute("SELECT code FROM development_sources WHERE id=?", (self.ids["source"],)).fetchone()[0], "QD8070")

    def test_missing_borrowed_source_dependency_is_rejected(self):
        changed = self.rewrite(self.package(), lambda data: data["development"][0].update(sources=[]))
        with self.assertRaises(ValueError):
            self.dst.preview(changed)

    def test_existing_revision_uuid_with_new_valid_sha_and_bytes_is_rejected(self):
        package = self.package()
        self.apply(package)
        replacement = b"%PDF-1.4\nchanged valid drawing\n%%EOF\n"
        with tarfile.open(package, "r:gz") as archive:
            members = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
        payload = json.loads(members["data.json"])
        revision = payload["development"][0]["revisions"][0]
        digest = hashlib.sha256(replacement).hexdigest()
        relative = f"{self.ids['project']}/{self.ids['revision']}/{digest}.pdf"
        revision.update(sha256=digest, size_bytes=len(replacement), storage_path=relative)
        members["data.json"] = json.dumps(payload).encode()
        members.pop(MEMBER_PREFIX + self.relative)
        members[MEMBER_PREFIX + relative] = replacement
        changed = self.root / "changed-sha.tar.gz"
        with tarfile.open(changed, "w:gz") as archive:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, "不可变历史"):
            self.dst.preview(changed)
        self.assertEqual((self.target_files / self.relative).read_bytes(), PDF)
        self.assertFalse((self.target_files / relative).exists())

    def test_non_whitelisted_attachment_type_is_rejected_in_preview(self):
        changed = self.rewrite(self.package(), lambda data: data["development"][0]["revisions"][0].update(original_name="run.exe", extension=".exe", content_type="application/octet-stream"))
        with self.assertRaises(ValueError):
            self.dst.preview(changed)
