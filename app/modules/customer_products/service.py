from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from .domain import (
    CUSTOMER_DRAWING_KINDS,
    CustomerDrawingFileReference,
    CustomerDrawingSlot,
    CustomerDrawingSummary,
    CustomerProduct,
    CustomerProductValidationError,
    QuotedProductOption,
    clean_bld_no,
    clean_kind,
    clean_note,
    clean_product_code,
    clean_product_name,
    clean_revision_label,
)
from .ports import (
    CustomerDrawingStorage,
    CustomerFilePayload,
    CustomerProductUnitOfWorkFactory,
    PreparedCustomerFileBatch,
    ProductCatalogPort,
    QuoteHistoryPort,
)


class _LocalFileUpload:
    """把磁盘上的既有文件（产品目录图纸）包装成上传管线可接受的形态。"""

    def __init__(self, path: Path, original_name: str) -> None:
        self.filename = original_name or path.name
        self.content_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"
        self.stream = path.open("rb")

    def close(self) -> None:
        self.stream.close()


class CustomerProductService:
    def __init__(
        self,
        unit_of_work_factory: CustomerProductUnitOfWorkFactory,
        storage: CustomerDrawingStorage,
        quote_history: QuoteHistoryPort | None = None,
        catalog: ProductCatalogPort | None = None,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.storage = storage
        self.quote_history = quote_history
        self.catalog = catalog

    @staticmethod
    def kinds() -> tuple[dict[str, str], ...]:
        return CUSTOMER_DRAWING_KINDS

    def list_for_customer(self, customer_id: int) -> list[CustomerProduct]:
        with self.unit_of_work_factory() as unit_of_work:
            if unit_of_work.repository.customer_identity(customer_id) is None:
                raise CustomerProductValidationError("customer.not_found", "客户不存在。")
            products = unit_of_work.repository.list_products(customer_id)
        return [self._with_catalog(product) for product in products]

    def _with_catalog(self, product: CustomerProduct) -> CustomerProduct:
        if self.catalog is None:
            return product
        return replace(product, catalog=self.catalog.info(product.bld_no))

    def quoted_product_options(self, customer_id: int) -> list[QuotedProductOption]:
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            if customer is None:
                raise CustomerProductValidationError("customer.not_found", "客户不存在。")
        if self.quote_history is None:
            return []
        return self.quote_history.quoted_products(customer_id, customer.name)

    def get(self, customer_id: int, product_id: int) -> CustomerProduct:
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.get_product(customer_id, product_id)
            if product is None:
                raise CustomerProductValidationError("customer_product.not_found", "客户商品不存在。")
            return product

    def summaries_for_customers(self, customer_ids: Sequence[int]) -> dict[int, CustomerDrawingSummary]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.summaries(customer_ids)

    def file_references(self, file_ids: Sequence[int]) -> dict[int, CustomerDrawingFileReference]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.file_references(file_ids)

    def create(
        self,
        customer_id: int,
        bld_no: object,
        code: object = "",
        name: object = "",
        *,
        actor: str,
    ) -> CustomerProduct:
        cleaned_bld_no = clean_bld_no(bld_no)
        cleaned_code = clean_product_code(code)
        cleaned_name = clean_product_name(name)
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            if customer is None:
                raise CustomerProductValidationError("customer.not_found", "客户不存在。")
            options = self.quote_history.quoted_products(customer_id, customer.name) if self.quote_history else []
            match = next(
                (option for option in options if option.bld_no.casefold() == cleaned_bld_no.casefold()),
                None,
            )
            if match is None:
                raise CustomerProductValidationError(
                    "customer_product.bld_not_quoted",
                    "该 BLD 号未出现在该客户的报价历史中，不能建立客户商品。",
                    field="bld_no",
                )
            if not cleaned_code:
                cleaned_code = match.customer_product_code
            if not cleaned_name and self.catalog is not None:
                info = self.catalog.info(cleaned_bld_no)
                cleaned_name = info.item_name if info is not None else ""
            product_sync_id = uuid4().hex
            product_id = unit_of_work.repository.insert_product(
                customer_id=customer.id,
                sync_id=product_sync_id,
                bld_no=cleaned_bld_no,
                customer_product_code=cleaned_code,
                customer_product_name=cleaned_name,
                actor=actor,
            )
            unit_of_work.repository.audit(
                "新增客户商品", product_sync_id, f"{customer.name}；{cleaned_bld_no}", actor=actor
            )
            result = unit_of_work.repository.get_product(customer.id, product_id)
            assert result is not None
            unit_of_work.commit()
            return result

    def update(
        self,
        customer_id: int,
        product_id: int,
        code: object = "",
        name: object = "",
        *,
        actor: str,
    ) -> CustomerProduct:
        # bld_no 一经建立不可修改。
        cleaned_code = clean_product_code(code)
        cleaned_name = clean_product_name(name)
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            product = unit_of_work.repository.get_product(customer_id, product_id)
            if customer is None or product is None:
                raise CustomerProductValidationError("customer_product.not_found", "客户商品不存在。")
            updated = unit_of_work.repository.update_product(
                customer_id,
                product_id,
                customer_product_code=cleaned_code,
                customer_product_name=cleaned_name,
                actor=actor,
            )
            if not updated:
                raise CustomerProductValidationError("customer_product.conflict", "客户商品已发生变化，请刷新后重试。")
            unit_of_work.repository.audit(
                "更新客户商品",
                product.sync_id,
                f"{customer.name}；{product.bld_no}",
                actor=actor,
            )
            result = unit_of_work.repository.get_product(customer_id, product_id)
            assert result is not None
            unit_of_work.commit()
            return result

    def upload_version(
        self,
        customer_id: int,
        product_id: int,
        kind: object,
        files: Sequence[object],
        *,
        revision_label: object = "",
        note: object = "",
        actor: str,
    ) -> CustomerProduct:
        cleaned_kind = clean_kind(kind)
        cleaned_revision = clean_revision_label(revision_label)
        cleaned_note = clean_note(note)
        return self._add_version(
            customer_id,
            product_id,
            cleaned_kind,
            files,
            revision_label=cleaned_revision,
            note=cleaned_note,
            audit_action="上传客户图纸版本",
            actor=actor,
        )

    def import_catalog_drawing(self, customer_id: int, product_id: int, *, actor: str) -> CustomerProduct:
        if self.catalog is None:
            raise CustomerProductValidationError(
                "customer_product.catalog_unavailable", "产品目录功能暂不可用，请稍后重试。"
            )
        product = self.get(customer_id, product_id)
        source = self.catalog.drawing_source(product.bld_no)
        if source is None:
            raise CustomerProductValidationError(
                "customer_product.catalog_drawing_missing",
                "产品目录中没有该 BLD 号的图纸，无法引入。",
            )
        upload = _LocalFileUpload(Path(source.path), source.original_name)
        try:
            return self._add_version(
                customer_id,
                product_id,
                "bld",
                (upload,),
                revision_label="",
                note="引入自产品目录图纸",
                audit_action="引入产品目录图纸",
                actor=actor,
            )
        finally:
            upload.close()

    def _add_version(
        self,
        customer_id: int,
        product_id: int,
        kind: str,
        files: Sequence[object],
        *,
        revision_label: str,
        note: str,
        audit_action: str,
        actor: str,
    ) -> CustomerProduct:
        prepared: PreparedCustomerFileBatch | None = None
        promoted = False
        try:
            with self.unit_of_work_factory() as unit_of_work:
                customer = unit_of_work.repository.customer_identity(customer_id)
                product = unit_of_work.repository.get_product(customer_id, product_id)
                if customer is None or product is None:
                    raise CustomerProductValidationError("customer_product.not_found", "客户商品不存在。")
                if not customer.sync_id:
                    raise CustomerProductValidationError(
                        "customer_drawing.customer_identity_missing", "客户缺少同步标识，暂时无法保存图纸。"
                    )
                slot = unit_of_work.repository.get_slot(customer_id, product.id, kind)
                if slot is None:
                    unit_of_work.repository.insert_slot(
                        customer_product_id=product.id,
                        customer_id=customer.id,
                        sync_id=uuid4().hex,
                        kind=kind,
                        actor=actor,
                    )
                    slot = unit_of_work.repository.get_slot(customer_id, product.id, kind)
                    assert slot is not None
                version_no = max((item.version_no for item in slot.files), default=0) + 1
                prepared = self.storage.prepare(
                    files,
                    customer_sync_id=customer.sync_id,
                    group_sync_id=slot.sync_id,
                    version_no=version_no,
                )
                claimed = unit_of_work.repository.claim_next_version(
                    customer_id,
                    slot.id,
                    expected_version=slot.current_version,
                    new_version=version_no,
                    actor=actor,
                )
                if not claimed:
                    raise CustomerProductValidationError(
                        "customer_drawing.version_conflict", "图纸版本已发生变化，请刷新后重试。"
                    )
                unit_of_work.repository.insert_file(
                    slot.id,
                    version_no,
                    prepared.files[0],
                    revision_label=revision_label,
                    note=note,
                    actor=actor,
                )
                self.storage.promote(prepared)
                promoted = True
                unit_of_work.repository.audit(
                    audit_action,
                    slot.sync_id,
                    f"{customer.name}；{product.bld_no}；{slot.kind_label}；版本 {version_no}",
                    actor=actor,
                )
                result = unit_of_work.repository.get_product(customer_id, product.id)
                assert result is not None
                unit_of_work.commit()
                return result
        except Exception:
            self._cleanup(prepared, promoted=promoted)
            raise

    def set_current_version(
        self,
        customer_id: int,
        product_id: int,
        kind: object,
        version_no: object,
        *,
        actor: str,
    ) -> CustomerProduct:
        cleaned_kind = clean_kind(kind)
        version_text = str(version_no or "").strip()
        # 长度上限防超长数字串：isdigit 为 True 后 int() 会因超过解释器位数上限抛原生 ValueError。
        if len(version_text) > 10 or not version_text.isdigit():
            raise CustomerProductValidationError(
                "customer_drawing.invalid_version", "版本号不正确。", field="version_no"
            )
        target_version = int(version_text)
        with self.unit_of_work_factory() as unit_of_work:
            customer = unit_of_work.repository.customer_identity(customer_id)
            product = unit_of_work.repository.get_product(customer_id, product_id)
            if customer is None or product is None:
                raise CustomerProductValidationError("customer_product.not_found", "客户商品不存在。")
            slot = unit_of_work.repository.get_slot(customer_id, product.id, cleaned_kind)
            if slot is None or not slot.files:
                raise CustomerProductValidationError(
                    "customer_drawing.slot_empty", "该图纸位还没有任何版本。", field="version_no"
                )
            if target_version not in {item.version_no for item in slot.files}:
                raise CustomerProductValidationError(
                    "customer_drawing.version_not_found", "指定版本不存在。", field="version_no"
                )
            if slot.current_version != target_version:
                changed = unit_of_work.repository.set_current_version(
                    customer_id, slot.id, target_version, actor=actor
                )
                if not changed:
                    raise CustomerProductValidationError(
                        "customer_drawing.version_conflict", "图纸版本已发生变化，请刷新后重试。"
                    )
                unit_of_work.repository.audit(
                    "设置当前图纸版本",
                    slot.sync_id,
                    f"{customer.name}；{product.bld_no}；{slot.kind_label}；版本 {target_version}",
                    actor=actor,
                )
            unit_of_work.commit()
            result = unit_of_work.repository.get_product(customer_id, product_id)
            assert result is not None
            return result

    def version_history(self, customer_id: int, product_id: int, kind: object) -> CustomerDrawingSlot | None:
        cleaned_kind = clean_kind(kind)
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.get_product(customer_id, product_id)
            if product is None:
                raise CustomerProductValidationError("customer_product.not_found", "客户商品不存在。")
            return unit_of_work.repository.get_slot(customer_id, product.id, cleaned_kind)

    def file_payload(
        self,
        customer_id: int,
        file_id: int,
        *,
        actor: str,
        for_preview: bool = False,
    ) -> CustomerFilePayload:
        # Preview/download are GET operations. Keep them strictly read-only;
        # access auditing would turn a safe request into a CSRF-triggerable write.
        with self.unit_of_work_factory() as unit_of_work:
            access = unit_of_work.repository.get_file_access(customer_id, file_id)
            if access is None:
                raise CustomerProductValidationError("customer_drawing.file_not_found", "图纸文件不存在。")
            if for_preview and not access.file.previewable:
                raise CustomerProductValidationError(
                    "customer_drawing.preview_not_supported", "该文件格式不支持在线预览，请下载查看。"
                )
            path = self.storage.resolve(
                access.file.storage_path,
                customer_sync_id=access.customer_sync_id,
                group_sync_id=access.group_sync_id,
            )
            if not self.storage.verify(path, size_bytes=access.file.size_bytes, sha256=access.file.sha256):
                raise CustomerProductValidationError(
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

    def _cleanup(self, prepared: PreparedCustomerFileBatch | None, *, promoted: bool) -> None:
        if prepared is None:
            return
        if promoted:
            self.storage.compensate(prepared)
        else:
            self.storage.discard(prepared)
