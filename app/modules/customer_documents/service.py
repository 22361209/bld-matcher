from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4

from .domain import (
    CUSTOMER_DOCUMENT_CATEGORIES,
    CustomerDocumentGroup,
    CustomerDocumentSummary,
    CustomerDocumentValidationError,
    clean_category,
    clean_description,
    clean_language,
    clean_title,
)
from .ports import (
    CustomerDocumentStorage,
    CustomerDocumentUnitOfWorkFactory,
    CustomerFilePayload,
    PreparedCustomerFileBatch,
)


class CustomerDocumentService:
    def __init__(
        self,
        unit_of_work_factory: CustomerDocumentUnitOfWorkFactory,
        storage: CustomerDocumentStorage,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.storage = storage

    @staticmethod
    def categories() -> tuple[dict[str, str], ...]:
        return CUSTOMER_DOCUMENT_CATEGORIES

    def list_for_customer(
        self,
        customer_id: int,
        *,
        include_archived: bool = False,
    ) -> list[CustomerDocumentGroup]:
        with self.unit_of_work_factory() as unit_of_work:
            if unit_of_work.repository.customer_identity(customer_id) is None:
                raise CustomerDocumentValidationError("customer.not_found", "客户不存在。")
            return unit_of_work.repository.list_groups(customer_id, include_archived=include_archived)

    def get(self, customer_id: int, group_id: int) -> CustomerDocumentGroup:
        with self.unit_of_work_factory() as unit_of_work:
            group = unit_of_work.repository.get_group(customer_id, group_id)
            if group is None:
                raise CustomerDocumentValidationError("customer_document.not_found", "客户资料不存在。")
            return group

    def summaries_for_customers(self, customer_ids: Sequence[int]) -> dict[int, CustomerDocumentSummary]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.summaries(customer_ids)

    def create(
        self,
        customer_id: int,
        data: Mapping[str, object],
        *,
        files: Sequence[object] = (),
        actor: str,
    ) -> CustomerDocumentGroup:
        category, title, description, language = self._clean_group_data(data)
        group_sync_id = uuid4().hex
        prepared: PreparedCustomerFileBatch | None = None
        promoted = False
        try:
            with self.unit_of_work_factory() as unit_of_work:
                customer = unit_of_work.repository.customer_identity(customer_id)
                if customer is None:
                    raise CustomerDocumentValidationError("customer.not_found", "客户不存在。")
                if not customer.sync_id:
                    raise CustomerDocumentValidationError(
                        "customer_document.customer_identity_missing", "客户缺少同步标识，暂时无法保存资料。"
                    )
                version_no = 1 if files else 0
                if files:
                    prepared = self.storage.prepare(
                        files,
                        customer_sync_id=customer.sync_id,
                        group_sync_id=group_sync_id,
                        version_no=version_no,
                    )
                group_id = unit_of_work.repository.insert_group(
                    customer_id=customer.id,
                    sync_id=group_sync_id,
                    category=category,
                    title=title,
                    description=description,
                    language=language,
                    current_version=version_no,
                    actor=actor,
                )
                if prepared is not None:
                    unit_of_work.repository.insert_files(group_id, version_no, prepared.files, actor=actor)
                    self.storage.promote(prepared)
                    promoted = True
                detail = f"{customer.name}；{title}；{len(prepared.files) if prepared else 0} 个文件"
                unit_of_work.repository.audit("新增客户资料", group_sync_id, detail, actor=actor)
                result = unit_of_work.repository.get_group(customer.id, group_id)
                assert result is not None
                unit_of_work.commit()
                return result
        except Exception:
            self._cleanup(prepared, promoted=promoted)
            raise

    def update(
        self,
        customer_id: int,
        group_id: int,
        data: Mapping[str, object],
        *,
        actor: str,
    ) -> CustomerDocumentGroup:
        category, title, description, language = self._clean_group_data(data)
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            group = unit_of_work.repository.get_group(customer_id, group_id)
            if customer is None or group is None:
                raise CustomerDocumentValidationError("customer_document.not_found", "客户资料不存在。")
            if group.archived:
                raise CustomerDocumentValidationError("customer_document.archived", "已归档资料不能修改。")
            updated = unit_of_work.repository.update_group(
                customer_id,
                group_id,
                category=category,
                title=title,
                description=description,
                language=language,
                actor=actor,
            )
            if not updated:
                raise CustomerDocumentValidationError("customer_document.conflict", "客户资料已发生变化，请刷新后重试。")
            unit_of_work.repository.audit("更新客户资料", group.sync_id, f"{customer.name}；{title}", actor=actor)
            result = unit_of_work.repository.get_group(customer_id, group_id)
            assert result is not None
            unit_of_work.commit()
            return result

    def add_version(
        self,
        customer_id: int,
        group_id: int,
        files: Sequence[object],
        *,
        actor: str,
    ) -> CustomerDocumentGroup:
        prepared: PreparedCustomerFileBatch | None = None
        promoted = False
        try:
            with self.unit_of_work_factory() as unit_of_work:
                customer = unit_of_work.repository.customer_identity(customer_id)
                group = unit_of_work.repository.get_group(customer_id, group_id)
                if customer is None or group is None:
                    raise CustomerDocumentValidationError("customer_document.not_found", "客户资料不存在。")
                if group.archived:
                    raise CustomerDocumentValidationError("customer_document.archived", "已归档资料不能上传新版本。")
                version_no = group.current_version + 1
                prepared = self.storage.prepare(
                    files,
                    customer_sync_id=customer.sync_id,
                    group_sync_id=group.sync_id,
                    version_no=version_no,
                )
                claimed = unit_of_work.repository.claim_next_version(
                    customer_id,
                    group_id,
                    expected_version=group.current_version,
                    actor=actor,
                )
                if claimed != version_no:
                    raise CustomerDocumentValidationError(
                        "customer_document.version_conflict", "资料版本已发生变化，请刷新后重试。"
                    )
                unit_of_work.repository.insert_files(group_id, version_no, prepared.files, actor=actor)
                self.storage.promote(prepared)
                promoted = True
                unit_of_work.repository.audit(
                    "上传客户资料版本",
                    group.sync_id,
                    f"{customer.name}；{group.title}；版本 {version_no}；{len(prepared.files)} 个文件",
                    actor=actor,
                )
                result = unit_of_work.repository.get_group(customer_id, group_id)
                assert result is not None
                unit_of_work.commit()
                return result
        except Exception:
            self._cleanup(prepared, promoted=promoted)
            raise

    def archive(self, customer_id: int, group_id: int, *, actor: str) -> CustomerDocumentGroup:
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            group = unit_of_work.repository.get_group(customer_id, group_id)
            if customer is None or group is None:
                raise CustomerDocumentValidationError("customer_document.not_found", "客户资料不存在。")
            if group.archived:
                return group
            if not unit_of_work.repository.archive_group(customer_id, group_id, actor=actor):
                raise CustomerDocumentValidationError("customer_document.conflict", "客户资料已发生变化，请刷新后重试。")
            unit_of_work.repository.audit(
                "归档客户资料", group.sync_id, f"{customer.name}；{group.title}", actor=actor
            )
            result = unit_of_work.repository.get_group(customer_id, group_id)
            assert result is not None
            unit_of_work.commit()
            return result

    def file_payload(
        self,
        customer_id: int,
        file_id: int,
        *,
        actor: str,
        for_preview: bool = False,
        allow_archived: bool = False,
    ) -> CustomerFilePayload:
        # Preview/download are GET operations. Keep them strictly read-only;
        # access auditing would turn a safe request into a CSRF-triggerable write.
        with self.unit_of_work_factory() as unit_of_work:
            access = unit_of_work.repository.get_file_access(customer_id, file_id)
            if access is None:
                raise CustomerDocumentValidationError("customer_document.file_not_found", "客户资料文件不存在。")
            if access.group_archived and not allow_archived:
                raise CustomerDocumentValidationError("customer_document.archived", "该客户资料已归档。")
            if for_preview and not access.file.previewable:
                raise CustomerDocumentValidationError(
                    "customer_document.preview_not_supported", "该文件格式不支持在线预览，请下载查看。"
                )
            path = self.storage.resolve(
                access.file.storage_path,
                customer_sync_id=access.customer_sync_id,
                group_sync_id=access.group_sync_id,
            )
            if not self.storage.verify(path, size_bytes=access.file.size_bytes, sha256=access.file.sha256):
                raise CustomerDocumentValidationError(
                    "customer_document.file_corrupt", "客户资料文件完整性校验失败，请联系管理员。"
                )
            return CustomerFilePayload(
                path=path,
                download_name=access.file.original_name,
                content_type=access.file.content_type,
                size_bytes=access.file.size_bytes,
                sha256=access.file.sha256,
                previewable=access.file.previewable,
            )

    @staticmethod
    def _clean_group_data(data: Mapping[str, object]) -> tuple[str, str, str, str]:
        return (
            clean_category(data.get("category")),
            clean_title(data.get("title")),
            clean_description(data.get("description")),
            clean_language(data.get("language")),
        )

    def _cleanup(self, prepared: PreparedCustomerFileBatch | None, *, promoted: bool) -> None:
        if prepared is None:
            return
        if promoted:
            self.storage.compensate(prepared)
        else:
            self.storage.discard(prepared)
