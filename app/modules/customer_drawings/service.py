from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import uuid4

from .domain import (
    CUSTOMER_DRAWING_DIRECTIONS,
    CustomerDrawingFileReference,
    CustomerDrawingGroup,
    CustomerDrawingSummary,
    CustomerDrawingValidationError,
    clean_bld_no,
    clean_direction,
    clean_drawing_no,
    clean_note,
    clean_revision_label,
    clean_title,
)
from .ports import (
    CustomerDrawingStorage,
    CustomerDrawingUnitOfWorkFactory,
    CustomerFilePayload,
    PreparedCustomerFileBatch,
)


class CustomerDrawingService:
    def __init__(
        self,
        unit_of_work_factory: CustomerDrawingUnitOfWorkFactory,
        storage: CustomerDrawingStorage,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.storage = storage

    @staticmethod
    def directions() -> tuple[dict[str, str], ...]:
        return CUSTOMER_DRAWING_DIRECTIONS

    def list_for_customer(
        self,
        customer_id: int,
        *,
        include_archived: bool = False,
    ) -> list[CustomerDrawingGroup]:
        with self.unit_of_work_factory() as unit_of_work:
            if unit_of_work.repository.customer_identity(customer_id) is None:
                raise CustomerDrawingValidationError("customer.not_found", "客户不存在。")
            return unit_of_work.repository.list_groups(customer_id, include_archived=include_archived)

    def get(self, customer_id: int, group_id: int) -> CustomerDrawingGroup:
        with self.unit_of_work_factory() as unit_of_work:
            group = unit_of_work.repository.get_group(customer_id, group_id)
            if group is None:
                raise CustomerDrawingValidationError("customer_drawing.not_found", "客户图纸不存在。")
            return group

    def summaries_for_customers(self, customer_ids: Sequence[int]) -> dict[int, CustomerDrawingSummary]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.summaries(customer_ids)

    def file_references(self, file_ids: Sequence[int]) -> dict[int, CustomerDrawingFileReference]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.file_references(file_ids)

    def create(
        self,
        customer_id: int,
        data: Mapping[str, object],
        *,
        files: Sequence[object] = (),
        actor: str,
    ) -> CustomerDrawingGroup:
        direction, bld_no, title, drawing_no = self._clean_group_data(data)
        group_sync_id = uuid4().hex
        prepared: PreparedCustomerFileBatch | None = None
        promoted = False
        try:
            with self.unit_of_work_factory() as unit_of_work:
                customer = unit_of_work.repository.customer_identity(customer_id)
                if customer is None:
                    raise CustomerDrawingValidationError("customer.not_found", "客户不存在。")
                if not customer.sync_id:
                    raise CustomerDrawingValidationError(
                        "customer_drawing.customer_identity_missing", "客户缺少同步标识，暂时无法保存图纸。"
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
                    direction=direction,
                    bld_no=bld_no,
                    title=title,
                    drawing_no=drawing_no,
                    current_version=version_no,
                    actor=actor,
                )
                if prepared is not None:
                    unit_of_work.repository.insert_file(
                        group_id,
                        version_no,
                        prepared.files[0],
                        revision_label=clean_revision_label(data.get("revision_label")),
                        note=clean_note(data.get("note")),
                        actor=actor,
                    )
                    self.storage.promote(prepared)
                    promoted = True
                detail = f"{customer.name}；{title}；{len(prepared.files) if prepared else 0} 个文件"
                unit_of_work.repository.audit("新增客户图纸", group_sync_id, detail, actor=actor)
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
    ) -> CustomerDrawingGroup:
        direction, bld_no, title, drawing_no = self._clean_group_data(data)
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            group = unit_of_work.repository.get_group(customer_id, group_id)
            if customer is None or group is None:
                raise CustomerDrawingValidationError("customer_drawing.not_found", "客户图纸不存在。")
            if group.archived:
                raise CustomerDrawingValidationError("customer_drawing.archived", "已归档图纸不能修改。")
            updated = unit_of_work.repository.update_group(
                customer_id,
                group_id,
                direction=direction,
                bld_no=bld_no,
                title=title,
                drawing_no=drawing_no,
                actor=actor,
            )
            if not updated:
                raise CustomerDrawingValidationError("customer_drawing.conflict", "客户图纸已发生变化，请刷新后重试。")
            unit_of_work.repository.audit("更新客户图纸", group.sync_id, f"{customer.name}；{title}", actor=actor)
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
        revision_label: str = "",
        note: str = "",
        actor: str,
    ) -> CustomerDrawingGroup:
        revision_label = clean_revision_label(revision_label)
        note = clean_note(note)
        prepared: PreparedCustomerFileBatch | None = None
        promoted = False
        try:
            with self.unit_of_work_factory() as unit_of_work:
                customer = unit_of_work.repository.customer_identity(customer_id)
                group = unit_of_work.repository.get_group(customer_id, group_id)
                if customer is None or group is None:
                    raise CustomerDrawingValidationError("customer_drawing.not_found", "客户图纸不存在。")
                if group.archived:
                    raise CustomerDrawingValidationError("customer_drawing.archived", "已归档图纸不能上传新版本。")
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
                    raise CustomerDrawingValidationError(
                        "customer_drawing.version_conflict", "图纸版本已发生变化，请刷新后重试。"
                    )
                unit_of_work.repository.insert_file(
                    group_id,
                    version_no,
                    prepared.files[0],
                    revision_label=revision_label,
                    note=note,
                    actor=actor,
                )
                self.storage.promote(prepared)
                promoted = True
                unit_of_work.repository.audit(
                    "上传客户图纸版本",
                    group.sync_id,
                    f"{customer.name}；{group.title}；版本 {version_no}",
                    actor=actor,
                )
                result = unit_of_work.repository.get_group(customer_id, group_id)
                assert result is not None
                unit_of_work.commit()
                return result
        except Exception:
            self._cleanup(prepared, promoted=promoted)
            raise

    def archive(self, customer_id: int, group_id: int, *, actor: str) -> CustomerDrawingGroup:
        return self._set_archived(customer_id, group_id, archived=True, actor=actor)

    def unarchive(self, customer_id: int, group_id: int, *, actor: str) -> CustomerDrawingGroup:
        return self._set_archived(customer_id, group_id, archived=False, actor=actor)

    def _set_archived(
        self,
        customer_id: int,
        group_id: int,
        *,
        archived: bool,
        actor: str,
    ) -> CustomerDrawingGroup:
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            group = unit_of_work.repository.get_group(customer_id, group_id)
            if customer is None or group is None:
                raise CustomerDrawingValidationError("customer_drawing.not_found", "客户图纸不存在。")
            if group.archived == archived:
                return group
            if archived:
                changed = unit_of_work.repository.archive_group(customer_id, group_id, actor=actor)
            else:
                changed = unit_of_work.repository.unarchive_group(customer_id, group_id, actor=actor)
            if not changed:
                raise CustomerDrawingValidationError("customer_drawing.conflict", "客户图纸已发生变化，请刷新后重试。")
            action = "归档客户图纸" if archived else "恢复客户图纸"
            unit_of_work.repository.audit(action, group.sync_id, f"{customer.name}；{group.title}", actor=actor)
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
                raise CustomerDrawingValidationError("customer_drawing.file_not_found", "图纸文件不存在。")
            if access.group_archived and not allow_archived:
                raise CustomerDrawingValidationError("customer_drawing.archived", "该客户图纸已归档。")
            if for_preview and not access.file.previewable:
                raise CustomerDrawingValidationError(
                    "customer_drawing.preview_not_supported", "该文件格式不支持在线预览，请下载查看。"
                )
            path = self.storage.resolve(
                access.file.storage_path,
                customer_sync_id=access.customer_sync_id,
                group_sync_id=access.group_sync_id,
            )
            if not self.storage.verify(path, size_bytes=access.file.size_bytes, sha256=access.file.sha256):
                raise CustomerDrawingValidationError(
                    "customer_drawing.file_corrupt", "图纸文件完整性校验失败，请联系管理员。"
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
            clean_direction(data.get("direction")),
            clean_bld_no(data.get("bld_no")),
            clean_title(data.get("title")),
            clean_drawing_no(data.get("drawing_no")),
        )

    def _cleanup(self, prepared: PreparedCustomerFileBatch | None, *, promoted: bool) -> None:
        if prepared is None:
            return
        if promoted:
            self.storage.compensate(prepared)
        else:
            self.storage.discard(prepared)
