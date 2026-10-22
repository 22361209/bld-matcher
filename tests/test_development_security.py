"""Adversarial development-register checks using synthetic data and files only."""
from __future__ import annotations

import copy
import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from werkzeug.datastructures import FileStorage

from app.database import connect
from app.modules.business_sync.infrastructure import BusinessSyncRepository
from app.modules.business_sync.service import BusinessSyncService
from app.modules.development.document_domain import DevelopmentDocumentError
from app.modules.development.document_service import DevelopmentDocumentService
from app.modules.development.domain import DevelopmentValidationError, validate_aggregate
from app.modules.development.file_store import DevelopmentFileStore
from app.modules.development.repository import SQLiteDevelopmentUnitOfWork
from app.modules.development.schema import install_schema
from app.modules.development.service import DevelopmentService


def new_id() -> str:
    return str(uuid4())


def aggregate_fixture() -> dict:
    project_id, branch_id, task_id, component_id = [new_id() for _ in range(4)]
    aggregate = {
        "project": {"id": project_id, "core_number": "8461", "title": "Synthetic security fixture"},
        "branches": [{"id": branch_id, "project_id": project_id, "code": "K8461LA"}],
        "tasks": [{"id": task_id, "project_id": project_id, "name": "Task"}],
        "components": [{"id": component_id, "project_id": project_id, "branch_id": branch_id,
                        "task_id": task_id, "name": "Component", "parent_id": None}],
        "operations": [], "suppliers": [], "documents": [], "revisions": [], "links": [],
    }
    # Populate authoritative database defaults without opening a runtime database.
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        install_schema(connection)
        for key, table in (("project", "development_projects"), ("branches", "development_branches"),
                           ("tasks", "development_tasks"), ("components", "development_components")):
            rows = [aggregate[key]] if key == "project" else aggregate[key]
            for row in rows:
                row.update(created_by="owner", created_at="2026-10-01 10:00:00", updated_at="2026-10-01 10:00:00")
                connection.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                                   list(row.values()))
            saved = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
            aggregate[key] = saved[0] if key == "project" else saved
    validate_aggregate(aggregate)
    return aggregate


class DevelopmentAggregateSecurityTest(unittest.TestCase):
    def test_foreign_project_and_missing_task_references_are_rejected(self) -> None:
        valid = aggregate_fixture()
        validate_aggregate(valid)
        for key, field in (("branches", "project_id"), ("tasks", "project_id"),
                           ("components", "project_id"), ("components", "task_id")):
            with self.subTest(group=key, field=field):
                aggregate = copy.deepcopy(valid)
                aggregate[key][0][field] = new_id()
                with self.assertRaises(DevelopmentValidationError):
                    validate_aggregate(aggregate)

    def test_component_cycle_and_cross_branch_parent_are_rejected(self) -> None:
        aggregate = aggregate_fixture()
        first = aggregate["components"][0]
        second = {**first, "id": new_id(), "parent_id": first["id"]}
        aggregate["components"].append(second)
        first["parent_id"] = second["id"]
        with self.assertRaises(DevelopmentValidationError):
            validate_aggregate(aggregate)
        first["parent_id"] = None
        branch = {**aggregate["branches"][0], "id": new_id(), "code": "K8461RA"}
        aggregate["branches"].append(branch)
        second["branch_id"] = branch["id"]
        with self.assertRaises(DevelopmentValidationError):
            validate_aggregate(aggregate)

    def test_uuid_reuse_across_entity_types_is_rejected(self) -> None:
        aggregate = aggregate_fixture()
        aggregate["components"][0]["id"] = aggregate["tasks"][0]["id"]
        with self.assertRaises(DevelopmentValidationError):
            validate_aggregate(aggregate)

    def test_active_component_cannot_reference_archived_task(self) -> None:
        aggregate = aggregate_fixture()
        aggregate["tasks"][0]["archived"] = 1
        with self.assertRaises(DevelopmentValidationError):
            validate_aggregate(aggregate)

    def test_confirmation_cannot_claim_a_different_owner(self) -> None:
        aggregate = aggregate_fixture()
        aggregate["branches"][0].update(owner_username="owner", confirmed_by="other-editor",
                                         confirmed_at="2026-10-01 10:00:00", actual_date="2026-10-01",
                                         production_ready=1)
        aggregate["tasks"][0].update(status="completed", owner_username="owner", actual_date="2026-10-01")
        with self.assertRaises(DevelopmentValidationError):
            validate_aggregate(aggregate)

    def test_document_link_cannot_reference_a_foreign_task(self) -> None:
        from app.modules.business_sync._development_documents import validate_documents

        aggregate = aggregate_fixture()
        project_id = aggregate["project"]["id"]
        document_id, revision_id = new_id(), new_id()
        digest = hashlib.sha256(b"synthetic").hexdigest()
        aggregate["documents"] = [{"id": document_id, "project_id": project_id}]
        aggregate["revisions"] = [{"id": revision_id, "project_id": project_id,
                                   "document_id": document_id, "sha256": digest,
                                   "extension": ".pdf", "size_bytes": 9,
                                   "storage_path": f"{project_id}/{revision_id}/{digest}.pdf"}]
        aggregate["links"] = [{"id": new_id(), "project_id": project_id,
                               "document_id": document_id, "revision_id": revision_id,
                               "target_type": "task", "target_id": new_id()}]
        with self.assertRaises(ValueError):
            validate_documents(aggregate)


class DevelopmentFilePathSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="bld-development-security-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_sync_rejects_symlinked_revision_directory(self) -> None:
        from app.modules.business_sync._development_files import safe_target

        storage, outside = self.root / "storage", self.root / "outside"
        storage.mkdir()
        outside.mkdir()
        (storage / "project").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_target(storage, "project/revision/file.pdf")

    def test_sync_rejects_hardlinked_existing_revision(self) -> None:
        from app.modules.business_sync._development_files import safe_target

        storage = self.root / "storage"
        target = storage / "project" / "revision" / "file.pdf"
        target.parent.mkdir(parents=True)
        outside = self.root / "synthetic-history.pdf"
        outside.write_bytes(b"synthetic history")
        os.link(outside, target)
        with self.assertRaises(ValueError):
            safe_target(storage, "project/revision/file.pdf")
        self.assertEqual(outside.read_bytes(), b"synthetic history")

    def test_sync_rejects_traversal_and_absolute_paths(self) -> None:
        from app.modules.business_sync._development_files import safe_target

        for relative in ("../outside.pdf", "/outside.pdf", "project/../../outside.pdf"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                safe_target(self.root / "storage", relative)

    def test_sync_rejects_existing_revision_uuid_with_a_different_unregistered_digest(self) -> None:
        from app.modules.business_sync._development_files import file_snapshot

        project_id, revision_id = new_id(), new_id()
        old_data, new_data = b"old synthetic revision", b"new synthetic revision"
        old_digest, new_digest = hashlib.sha256(old_data).hexdigest(), hashlib.sha256(new_data).hexdigest()
        storage = self.root / "storage"
        historical = storage / project_id / revision_id / f"{old_digest}.pdf"
        historical.parent.mkdir(parents=True)
        historical.write_bytes(old_data)
        incoming = {"id": revision_id, "project_id": project_id, "sha256": new_digest,
                    "extension": ".pdf", "size_bytes": len(new_data),
                    "storage_path": f"{project_id}/{revision_id}/{new_digest}.pdf"}
        with self.assertRaises(ValueError):
            file_snapshot(storage, [{"revisions": [incoming]}])
        self.assertEqual(historical.read_bytes(), old_data)


class DevelopmentFileCompensationSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="bld-development-compensation-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = DevelopmentFileStore(self.root / "files")
        self.project_id, self.revision_id = new_id(), new_id()

    def prepare(self, content: bytes = b"%PDF-1.4\nsynthetic-a\n%%EOF\n"):
        return self.store.prepare(
            FileStorage(stream=io.BytesIO(content), filename="synthetic.pdf", content_type="application/pdf"),
            project_id=self.project_id, revision_id=self.revision_id,
        )

    def test_repeated_publication_failure_cannot_remove_existing_history(self) -> None:
        historical = self.prepare()
        self.assertTrue(self.store.promote(historical))
        expected = historical.destination_path.read_bytes()
        repeated = self.prepare()
        self.assertFalse(self.store.promote(repeated))
        self.store.compensate(repeated)
        self.assertEqual(historical.destination_path.read_bytes(), expected)

    def test_compensation_refuses_to_delete_replacement_inode(self) -> None:
        prepared = self.prepare()
        self.store.promote(prepared)
        replacement = self.root / "replacement.pdf"
        replacement.write_bytes(b"unrelated synthetic history")
        os.replace(replacement, prepared.destination_path)
        with self.assertRaises(DevelopmentDocumentError):
            self.store.compensate(prepared)
        self.assertEqual(prepared.destination_path.read_bytes(), b"unrelated synthetic history")

    def test_same_revision_with_different_digest_cannot_replace_history(self) -> None:
        historical = self.prepare()
        self.store.promote(historical)
        expected = historical.destination_path.read_bytes()
        changed = self.prepare(b"%PDF-1.4\nsynthetic-b\n%%EOF\n")
        self.addCleanup(self.store.discard, changed)
        with self.assertRaises(DevelopmentDocumentError):
            self.store.promote(changed)
        self.assertEqual(historical.destination_path.read_bytes(), expected)
        self.assertFalse(changed.destination_path.exists())

    def test_hardlink_is_not_a_valid_immutable_revision(self) -> None:
        prepared = self.prepare()
        self.store.promote(prepared)
        os.link(prepared.destination_path, self.root / "mutable-alias.pdf")
        self.assertFalse(self.store.verify(
            prepared.destination_path, size_bytes=prepared.size_bytes, sha256=prepared.sha256,
        ))

    def test_changed_staged_bytes_are_rejected_before_publication(self) -> None:
        prepared = self.prepare()
        self.addCleanup(self.store.discard, prepared)
        prepared.temporary_path.write_bytes(b"%PDF-1.4\nchanged-bad\n%%EOF\n")
        with self.assertRaises(DevelopmentDocumentError):
            self.store.promote(prepared)
        self.assertFalse(prepared.destination_path.exists())

    def test_sync_compensation_continues_after_refusing_a_replaced_file(self) -> None:
        from app.modules.business_sync._development_files import restore_development_files

        first = self.prepare()
        self.store.promote(first)
        self.revision_id = new_id()
        second = self.prepare()
        self.store.promote(second)
        replacement = self.root / "replacement.pdf"
        replacement.write_bytes(b"unrelated replacement history")
        os.replace(replacement, second.destination_path)
        with self.assertRaises(ValueError):
            restore_development_files([(self.store, first), (self.store, second)])
        self.assertFalse(first.destination_path.exists(), "One failed compensation must not skip other owned files")
        self.assertEqual(second.destination_path.read_bytes(), b"unrelated replacement history")


class DevelopmentServiceSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="bld-development-service-security-")
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "synthetic.sqlite3"
        with connect(self.database):
            pass
        self.service = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.database))
        self.detail = self.service.create_project({"title": "Synthetic", "core_number": "8461"}, actor="owner")
        self.project_id = self.detail["project"]["id"]
        self.call("add_branch", {"code": "K8461LA", "owner_username": "owner", "production_ready": 1,
                                "drawing_required": 0, "drawing_reason": "Synthetic fixture"})
        self.branch_id = self.detail["branches"][0]["id"]
        self.call("add_component", self.branch_id, {"name": "Synthetic component", "task": {
            "drawing_required": 0, "drawing_reason": "Synthetic fixture requires no drawing",
            "owner_username": "owner", "status": "completed", "actual_date": "2026-10-01",
        }})
        self.component_id = self.detail["components"][0]["id"]
        self.task_id = self.detail["tasks"][0]["id"]

    def call(self, method: str, *args, **kwargs) -> dict:
        self.detail = getattr(self.service, method)(
            self.project_id, *args, expected_version=self.detail["project"]["version"],
            actor=kwargs.pop("actor", "owner"), **kwargs,
        )
        return self.detail

    def test_other_actor_cannot_confirm_and_failure_preserves_version(self) -> None:
        version = self.detail["project"]["version"]
        with self.assertRaises(DevelopmentValidationError):
            self.call("confirm_branch", self.branch_id, actor="other-editor")
        actual = self.service.detail(self.project_id)
        self.assertEqual(actual["project"]["version"], version)
        self.assertEqual(actual["branches"][0]["confirmed_by"], "")
        self.call("confirm_branch", self.branch_id)
        self.assertEqual(self.detail["project"]["status"], "completed")

    def test_shared_task_edit_invalidates_all_confirmed_branches(self) -> None:
        self.call("confirm_branch", self.branch_id)
        self.call("copy_branch", self.branch_id, {"code": "K8461RA", "owner_username": "owner",
                  "drawing_required": 0, "drawing_reason": "Synthetic fixture"},
                  modes={self.component_id: "shared"})
        second = next(row for row in self.detail["branches"] if row["id"] != self.branch_id)
        self.call("update_branch", second["id"], {"production_ready": 1})
        self.call("confirm_branch", second["id"])
        self.assertTrue(all(row["confirmed_by"] == "owner" for row in self.detail["branches"]))
        self.call("update_task", self.task_id, {"notes": "Changed engineering inputs"})
        self.assertTrue(all(row["confirmed_by"] == "" for row in self.detail["branches"]))
        self.assertTrue(all(row["actual_date"] == "" for row in self.detail["branches"]))
        self.assertNotEqual(self.detail["project"]["status"], "completed")

    def test_independent_copy_resets_task_and_operation_completion(self) -> None:
        self.call("add_operation", self.task_id, {"sequence": 1, "name": "Synthetic operation",
                  "status": "completed", "owner_username": "owner", "actual_date": "2026-10-01"})
        self.call("copy_branch", self.branch_id, {"code": "K8461RA", "owner_username": "owner",
                  "production_ready": 1}, modes={self.component_id: "independent"})
        copied_task = next(row for row in self.detail["tasks"] if row["id"] != self.task_id)
        copied_operation = next(row for row in self.detail["operations"] if row["task_id"] == copied_task["id"])
        for row in (copied_task, copied_operation):
            self.assertEqual(row["status"], "not_started")
            self.assertEqual(row["actual_date"], "")
        copied_branch = next(row for row in self.detail["branches"] if row["id"] != self.branch_id)
        self.assertEqual(copied_branch["production_ready"], 0)
        self.assertEqual(copied_branch["confirmed_by"], "")

    def test_service_rejects_foreign_task_and_rolls_back_entire_mutation(self) -> None:
        other = self.service.create_project({"title": "Foreign", "core_number": "9123"}, actor="owner")
        other_id = other["project"]["id"]
        other = self.service.add_branch(other_id, {"code": "K9123LA"},
                                        expected_version=other["project"]["version"], actor="owner")
        other_branch = other["branches"][0]["id"]
        other = self.service.add_component(other_id, other_branch, {"name": "Foreign component", "task": {
            "drawing_required": 0, "drawing_reason": "Synthetic",
        }}, expected_version=other["project"]["version"], actor="owner")
        version = self.detail["project"]["version"]
        with self.assertRaises(DevelopmentValidationError):
            self.call("add_component", self.branch_id, {"name": "Bad reference", "task_id": other["tasks"][0]["id"]})
        actual = self.service.detail(self.project_id)
        self.assertEqual(actual["project"]["version"], version)
        self.assertEqual(len(actual["components"]), 1)

    def test_copy_with_changed_borrowing_preserves_original_source_snapshot(self) -> None:
        self.call("update_component", self.component_id, {"borrowed_code": "SYNTHETIC-SOURCE-A"})
        original_source = self.detail["components"][0]["borrowed_source_id"]
        self.call("copy_branch", self.branch_id, {
            "code": "K8461RA", "owner_username": "owner",
            "component_codes": {self.component_id: {"borrowed_code": "SYNTHETIC-SOURCE-B"}},
        }, modes={self.component_id: "independent"})
        original = next(row for row in self.detail["components"] if row["id"] == self.component_id)
        copied = next(row for row in self.detail["components"] if row["id"] != self.component_id)
        self.assertEqual(original["borrowed_source_id"], original_source)
        self.assertEqual(original["borrowed_code"], "SYNTHETIC-SOURCE-A")
        self.assertEqual(copied["borrowed_code"], "SYNTHETIC-SOURCE-B")
        self.assertNotEqual(copied["borrowed_source_id"], original_source)


class DevelopmentSyncSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="bld-development-sync-security-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.target = self.root / "source.sqlite3", self.root / "target.sqlite3"
        with connect(self.source), connect(self.target):
            pass
        self.source_files, self.target_files = self.root / "source_files", self.root / "target_files"
        self.source_core = DevelopmentService(lambda: SQLiteDevelopmentUnitOfWork(self.source))
        self.documents = DevelopmentDocumentService(
            lambda: SQLiteDevelopmentUnitOfWork(self.source), DevelopmentFileStore(self.source_files),
        )
        self.source_sync = BusinessSyncService(BusinessSyncRepository(
            self.source, development_file_dir=self.source_files,
        ))
        self.target_repo = BusinessSyncRepository(self.target, development_file_dir=self.target_files)
        self.target_sync = BusinessSyncService(self.target_repo)
        self.package = self.root / "package.tar.gz"
        self.project = self.source_core.create_project({"title": "Previewed title", "core_number": "8461"}, actor="owner")
        self.project_id = self.project["project"]["id"]

    def export(self) -> None:
        self.source_sync.export(output_path=self.package, selected=("development",), actor="owner")

    def apply(self, preview: dict, *, selected: set[str] | None = None) -> dict:
        return self.target_sync.apply(
            self.package, backup_path=self.root / f"backup-{uuid4().hex}.sqlite3", actor="owner",
            expected_token=preview["token"], selected_conflicts={"development": selected or set()},
        )

    def database_apply_globals(self) -> dict:
        # Web integration fixtures reload app modules. Follow this repository's
        # actual function references instead of patching a newer sys.modules copy.
        adapter = self.target_repo.apply.__func__.__globals__["apply_package"]
        implementation = adapter.__globals__.get("apply_database_package", adapter)
        return implementation.__globals__

    def upload(self, project_id: str, label: str) -> tuple[dict, str]:
        current = self.source_core.detail(project_id)
        revision = self.documents.upload(project_id, {
            "kind": "drawing", "number": f"SYN-{label}", "title": "Synthetic drawing",
            "label": label, "change_note": "Synthetic revision", "state": "released",
        }, FileStorage(stream=io.BytesIO(f"%PDF-1.4\nsynthetic-{label}\n%%EOF\n".encode()),
                       filename="synthetic.pdf", content_type="application/pdf"),
            actor="owner", expected_version=current["project"]["version"])
        relative = f"{project_id}/{revision['id']}/{revision['sha256']}{revision['extension']}"
        return revision, relative

    def test_apply_never_uses_unpreviewed_payload_during_archive_aba_replacement(self) -> None:
        self.export()
        original_bytes = self.package.read_bytes()
        preview = self.target_sync.preview(self.package)
        self.source_core.update_project(self.project_id, {"title": "Unpreviewed replacement"},
                                        expected_version=self.project["project"]["version"], actor="owner")
        self.export()
        replacement_bytes = self.package.read_bytes()
        self.package.write_bytes(original_bytes)
        read_package = self.target_repo.read

        def replace_original_while_reading(read_path):
            self.package.write_bytes(replacement_bytes)
            try:
                return read_package(read_path)
            finally:
                self.package.write_bytes(original_bytes)

        try:
            with patch.object(self.target_repo, "read", side_effect=replace_original_while_reading):
                self.apply(preview)
        except ValueError:
            pass
        with connect(self.target) as connection:
            actual = connection.execute("SELECT title FROM development_projects WHERE id=?", (self.project_id,)).fetchone()
        self.assertTrue(actual is None or actual["title"] == "Previewed title",
                        "Applied a different package payload than the one shown during preview")

    def test_preview_token_binds_actual_local_revision_bytes(self) -> None:
        _revision, relative = self.upload(self.project_id, "A")
        self.export()
        self.apply(self.target_sync.preview(self.package))
        preview = self.target_sync.preview(self.package)
        local_file = self.target_files / relative
        local_file.write_bytes(b"%PDF-1.4\nlocally modified\n%%EOF\n")
        with self.assertRaises(ValueError):
            self.apply(preview)
        self.assertEqual(local_file.read_bytes(), b"%PDF-1.4\nlocally modified\n%%EOF\n")

    def test_preview_never_binds_a_token_to_different_archive_bytes(self) -> None:
        self.export()
        original_bytes = self.package.read_bytes()
        self.source_core.update_project(self.project_id, {"title": "Replacement shown at preview"},
                                        expected_version=self.project["project"]["version"], actor="owner")
        self.export()
        replacement_bytes = self.package.read_bytes()
        self.package.write_bytes(original_bytes)
        read_package = self.target_repo.read
        seen = {}

        def replace_original_while_previewing(read_path):
            self.package.write_bytes(replacement_bytes)
            try:
                manifest, payload = read_package(read_path)
                seen["title"] = payload["development"][0]["project"]["title"]
                return manifest, payload
            finally:
                self.package.write_bytes(original_bytes)

        try:
            with patch.object(self.target_repo, "read", side_effect=replace_original_while_previewing):
                preview = self.target_sync.preview(self.package)
            self.apply(preview)
        except ValueError:
            pass
        with connect(self.target) as connection:
            actual = connection.execute("SELECT title FROM development_projects WHERE id=?", (self.project_id,)).fetchone()
        self.assertTrue(actual is None or actual["title"] == seen["title"],
                        "Preview token admitted package bytes different from its parsed preview payload")

    def test_audit_failure_removes_only_new_files_and_keeps_accepted_history(self) -> None:
        _revision, old_relative = self.upload(self.project_id, "A")
        self.export()
        self.apply(self.target_sync.preview(self.package))
        old_bytes = (self.target_files / old_relative).read_bytes()
        _revision, new_relative = self.upload(self.project_id, "B")
        self.export()
        preview = self.target_sync.preview(self.package)
        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("synthetic audit failure")

        with patch.dict(self.database_apply_globals(), {"log_event": fail_audit}):
            with self.assertRaises(RuntimeError):
                self.apply(preview, selected={self.project_id})
        self.assertEqual((self.target_files / old_relative).read_bytes(), old_bytes)
        self.assertFalse((self.target_files / new_relative).exists())
        with connect(self.target) as connection:
            count = connection.execute("SELECT COUNT(*) FROM development_document_revisions WHERE project_id=?",
                                       (self.project_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_digest_is_rechecked_after_file_copy_and_before_commit(self) -> None:
        _revision, relative = self.upload(self.project_id, "A")
        self.export()
        preview = self.target_sync.preview(self.package)
        real_audit = self.database_apply_globals()["log_event"]

        def change_file_after_audit(*args, **kwargs):
            real_audit(*args, **kwargs)
            (self.target_files / relative).write_bytes(b"%PDF-1.4\nlate change\n%%EOF\n")

        with patch.dict(self.database_apply_globals(), {"log_event": change_file_after_audit}):
            with self.assertRaises(ValueError):
                self.apply(preview)
        with connect(self.target) as connection:
            count = connection.execute("SELECT COUNT(*) FROM development_projects").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertFalse((self.target_files / relative).exists())

    def test_unaccepted_conflict_does_not_copy_its_new_revision_file(self) -> None:
        _revision, old_relative = self.upload(self.project_id, "A")
        self.export()
        self.apply(self.target_sync.preview(self.package))
        old_bytes = (self.target_files / old_relative).read_bytes()
        _new_revision, rejected_relative = self.upload(self.project_id, "B")
        second = self.source_core.create_project({"title": "New accepted project", "core_number": "9123"}, actor="owner")
        second_id = second["project"]["id"]
        _accepted_revision, accepted_relative = self.upload(second_id, "C")
        self.export()
        result = self.apply(self.target_sync.preview(self.package))
        self.assertEqual(result["development"]["conflict"], 1)
        self.assertEqual(result["development"]["new"], 1)
        self.assertFalse((self.target_files / rejected_relative).exists())
        self.assertTrue((self.target_files / accepted_relative).is_file())
        self.assertEqual((self.target_files / old_relative).read_bytes(), old_bytes)
        with connect(self.target) as connection:
            count = connection.execute("SELECT COUNT(*) FROM development_document_revisions WHERE project_id=?",
                                       (self.project_id,)).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
