from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from .document_domain import DevelopmentDocumentError, required_text
from .file_store import DevelopmentFileStore, legacy_source
from .file_types import DevelopmentFilePayload
from .file_validation import DRAWING_EXTENSIONS
from .ports import UnitOfWorkFactory


logger = logging.getLogger(__name__)


class DevelopmentDocumentService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory, storage: DevelopmentFileStore,
                 *, legacy_roots: Mapping[str, Path] | None = None):
        self.unit_of_work_factory = unit_of_work_factory
        self.storage = storage
        self.legacy_roots = dict(legacy_roots or {})

    def list_documents(self, project_id: str, *, target_type: str | None = None, target_id: str | None = None) -> list[dict]:
        with self.unit_of_work_factory() as work:
            return work.documents.list_documents(
                project_id, target_type=target_type, target_id=target_id,
            )

    def upload(self, project_id: str, data: Mapping[str, object], upload: object, *, actor: str,
               expected_version: int, expected_sha256: str | None = None) -> dict:
        actor = required_text(actor, "已认证操作人")
        revision_id = str(uuid4())
        state = str(data.get("state") or "draft")
        if state not in {"draft", "released", "obsolete"}:
            raise DevelopmentDocumentError("development.document_state", "资料状态无效。")
        metadata = {"label": required_text(data.get("label"), "修订版本", limit=80),
                    "change_note": required_text(data.get("change_note"), "变更说明", limit=2000), "state": state}
        # Validate and hash before opening the short database write transaction.
        prepared = self.storage.prepare(upload, project_id=project_id, revision_id=revision_id, expected_sha256=expected_sha256)
        try:
            with self.unit_of_work_factory() as work:
                work.repository.claim_project(project_id, expected_version)
                repository = work.documents
                document_id = str(data.get("document_id") or "")
                if document_id:
                    document = repository.document(project_id, document_id)
                    if document["archived"]:
                        raise DevelopmentDocumentError("development.document_archived", "资料已归档，不能添加修订。")
                else:
                    kind = str(data.get("kind") or "drawing")
                    if kind not in {"drawing", "process"}:
                        raise DevelopmentDocumentError("development.document_kind", "资料类型无效。")
                    document = repository.create_document(project_id, {
                        "kind": kind, "number": required_text(data.get("number"), "资料编号"),
                        "title": required_text(data.get("title"), "资料名称"),
                    }, actor=actor)
                if document["kind"] == "drawing" and prepared.extension not in DRAWING_EXTENSIONS:
                    raise DevelopmentDocumentError("development.drawing_extension", "图纸仅支持 PDF 或白名单 CAD 源文件。")
                revision = repository.insert_revision(project_id, document["id"], {
                    **metadata, "id": revision_id, "original_name": prepared.original_name,
                    "extension": prepared.extension, "content_type": prepared.content_type,
                    "size_bytes": prepared.size_bytes, "sha256": prepared.sha256, "storage_path": prepared.storage_path,
                }, actor=actor)
                # Existing documents deliberately retain every target's selected old revision.
                if not document_id and (data.get("target_type") or data.get("target_id")):
                    repository.adopt(project_id, revision_id, target_type=str(data.get("target_type") or ""),
                                     target_id=str(data.get("target_id") or ""), actor=actor)
                    work.repository.invalidate_confirmations(project_id)
                repository.audit("development.document.upload", project_id, f"{document['id']}:{revision_id}:{prepared.sha256}", actor=actor)
                self.storage.promote(prepared)
                work.commit()
            return self._public_revision(revision)
        except BaseException:
            self.storage.compensate(prepared)
            raise
        finally:
            self.storage.discard(prepared)

    def adopt(self, project_id: str, revision_id: str, *, target_type: str, target_id: str, actor: str, expected_version: int) -> dict:
        actor = required_text(actor, "已认证操作人")
        with self.unit_of_work_factory() as work:
            work.repository.claim_project(project_id, expected_version)
            repository = work.documents
            revision = repository.revision(project_id, revision_id)
            self._verified_payload(revision)
            link = repository.adopt(project_id, revision_id, target_type=target_type, target_id=target_id, actor=actor)
            work.repository.invalidate_confirmations(project_id)
            repository.audit("development.document.adopt", project_id, f"{target_type}:{target_id}:{revision_id}", actor=actor)
            work.commit()
            return link

    def file_payload(self, project_id: str, revision_id: str) -> DevelopmentFilePayload:
        with self.unit_of_work_factory() as work:
            revision = work.documents.revision(project_id, revision_id)
            return self._verified_payload(revision)

    def has_valid_released_drawing(self, project_id: str, target_type: str, target_id: str) -> bool:
        with self.unit_of_work_factory() as work:
            revisions = work.documents.released_drawings(project_id, target_type, target_id)
            for revision in revisions:
                try:
                    self._verified_payload(revision)
                    return True
                except (DevelopmentDocumentError, OSError):
                    logger.warning("Development adopted drawing failed integrity check",
                                   extra={"project_id": project_id, "revision_id": revision["id"]})
        return False

    def _verified_payload(self, revision: Mapping[str, object]) -> DevelopmentFilePayload:
        expected_path = f"{revision['project_id']}/{revision['id']}/{revision['sha256']}{revision['extension']}"
        if revision["storage_path"] != expected_path:
            raise DevelopmentDocumentError("development.file_identity", "修订文件引用与归属不一致。")
        path = self.storage.resolve(str(revision["storage_path"]))
        if not self.storage.verify(path, size_bytes=int(str(revision["size_bytes"])), sha256=str(revision["sha256"])):
            raise DevelopmentDocumentError("development.file_integrity", "修订文件校验失败，请联系管理员。")
        return DevelopmentFilePayload(path, str(revision["original_name"]), str(revision["content_type"]),
                                      int(str(revision["size_bytes"])), str(revision["sha256"]), revision["content_type"] == "application/pdf")

    def set_state(self, project_id: str, revision_id: str, state: str, *, change_note: str, actor: str, expected_version: int) -> dict:
        with self.unit_of_work_factory() as work:
            revision = work.documents.revision(project_id, revision_id)
            payload = self._verified_payload(revision)
        with payload.path.open("rb") as handle:
            upload = SimpleNamespace(filename=payload.download_name, content_type=payload.content_type, stream=handle)
            return self.upload(project_id, {"document_id": revision["document_id"], "label": revision["label"],
                               "state": state, "change_note": change_note}, upload, actor=actor,
                               expected_version=expected_version, expected_sha256=payload.sha256)

    def archive(self, project_id: str, document_id: str, *, actor: str, expected_version: int) -> None:
        actor = required_text(actor, "已认证操作人")
        with self.unit_of_work_factory() as work:
            work.repository.claim_project(project_id, expected_version)
            repository = work.documents
            repository.archive(project_id, document_id)
            work.repository.invalidate_confirmations(project_id)
            repository.audit("development.document.archive", project_id, document_id, actor=actor)
            work.commit()

    def snapshot_legacy(self, project_id: str, data: Mapping[str, object], *, source_kind: str, relative_name: str,
                        actor: str, expected_version: int) -> dict:
        root = self.legacy_roots.get(source_kind)
        if root is None:
            raise DevelopmentDocumentError("development.legacy_source", "旧图纸来源无效。")
        source = legacy_source(root, relative_name)
        if source.suffix.lower() != ".pdf":
            raise DevelopmentDocumentError("development.legacy_type", "旧图纸快照仅支持 PDF。")
        with source.open("rb") as handle:
            upload = SimpleNamespace(filename=source.name, content_type="application/pdf", stream=handle)
            return self.upload(project_id, data, upload, actor=actor, expected_version=expected_version)

    @staticmethod
    def _public_revision(revision: Mapping[str, object]) -> dict:
        result = dict(revision)
        result.pop("storage_path", None)
        result["previewable"] = result["content_type"] == "application/pdf"
        return result
