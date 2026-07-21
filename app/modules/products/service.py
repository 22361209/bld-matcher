from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.matcher import ProductCatalog

from .catalog_import import (
    CatalogImportFileTransaction,
    CatalogImportChoices,
    CatalogImportPreview,
    CatalogImportPreviewChangedError,
    CatalogImportResult,
    CatalogImportStorage,
    build_catalog_import_preview,
    read_catalog_import,
)

from .brand_normalization import (
    BrandNormalizationPreview,
    BrandNormalizationPreviewChangedError,
    BrandNormalizationResult,
    build_brand_normalization_preview,
)
from .domain import (
    ProductFilterOptions,
    ProductFilters,
    ProductPage,
    ProductRecord,
    ProductStats,
    build_product_filters,
)
from .ports import CatalogBootstrapPort, LegacyAliasPort, ProductUnitOfWorkFactory


class ProductNotFoundError(LookupError):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"产品 {product_id} 不存在。")
        self.product_id = product_id


class ProductVersionConflictError(RuntimeError):
    def __init__(self, product_id: int, current_updated_at: str) -> None:
        super().__init__("产品已被其他操作更新，请先重新读取后再更新单价。")
        self.product_id = product_id
        self.current_updated_at = current_updated_at


class ProductService:
    def __init__(
        self,
        unit_of_work_factory: ProductUnitOfWorkFactory,
        bootstrap_port: CatalogBootstrapPort,
        legacy_alias_port: LegacyAliasPort,
        catalog_import_storage: CatalogImportStorage | None = None,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.bootstrap_port = bootstrap_port
        self.legacy_alias_port = legacy_alias_port
        self.catalog_import_storage = catalog_import_storage
        self._catalog_lock = Lock()
        self._catalog_version: tuple[object, ...] | None = None
        self._catalog: ProductCatalog | None = None

    def search(
        self,
        filters: Mapping[str, object] | ProductFilters,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> ProductPage:
        self.bootstrap_port()
        normalized = build_product_filters(filters)
        safe_limit = max(1, min(500, int(limit)))
        safe_offset = max(0, int(offset))
        with self.unit_of_work_factory() as unit_of_work:
            total = unit_of_work.repository.count(normalized)
            records = unit_of_work.repository.list(normalized, limit=safe_limit, offset=safe_offset)
        return ProductPage(records=records, total=total, limit=safe_limit, offset=safe_offset)

    def get(self, product_id: int) -> ProductRecord:
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.get(product_id)
        if product is None:
            raise ProductNotFoundError(product_id)
        return product

    def filter_options(
        self,
        filters: Mapping[str, object] | ProductFilters,
    ) -> ProductFilterOptions:
        self.bootstrap_port()
        normalized = build_product_filters(filters)
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.filter_options(normalized)

    def find_by_bld(self, bld_no: str, *, active_only: bool = True) -> ProductRecord | None:
        self.bootstrap_port()
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.get_by_bld(str(bld_no or "").strip())
        if product is None or (active_only and not product.active):
            return None
        return product

    def stats(self) -> ProductStats:
        self.bootstrap_port()
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.stats()

    def _load_catalog(self) -> ProductCatalog | None:
        legacy_aliases = self.legacy_alias_port()
        with self.unit_of_work_factory() as unit_of_work:
            version = unit_of_work.repository.catalog_version()
        version = (*version, tuple(sorted(legacy_aliases.items())))
        with self._catalog_lock:
            if version == self._catalog_version:
                return self._catalog
        with self.unit_of_work_factory() as unit_of_work:
            version, rows, aliases = unit_of_work.repository.catalog_snapshot()
        version = (*version, tuple(sorted(legacy_aliases.items())))
        with self._catalog_lock:
            aliases.update(legacy_aliases)
            self._catalog = ProductCatalog(rows, manual_map=aliases) if rows else None
            self._catalog_version = version
            return self._catalog

    def catalog(self) -> ProductCatalog | None:
        self.bootstrap_port()
        return self._load_catalog()

    def warm_catalog(self) -> ProductCatalog | None:
        """Build the read-only in-memory index after single-process initialization."""
        return self._load_catalog()

    def invalidate_catalog(self) -> None:
        with self._catalog_lock:
            self._catalog_version = None
            self._catalog = None

    def save(
        self,
        data: Mapping[str, object],
        *,
        actor: str,
        image_files: list[tuple[int, object]] | None = None,
        drawing_file: object | None = None,
    ) -> ProductRecord:
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.upsert(data, actor=actor)
            for slot, file in image_files or []:
                product = unit_of_work.repository.save_image(product.id, file, slot=slot, actor=actor)
            if drawing_file is not None:
                product = unit_of_work.repository.save_drawing(product.id, drawing_file, actor=actor)
            unit_of_work.commit()
        self.invalidate_catalog()
        return product

    def copy_as_new(
        self,
        source_product_id: int,
        data: Mapping[str, object],
        *,
        actor: str,
        image_files: list[tuple[int, object]] | None = None,
        drawing_file: object | None = None,
    ) -> ProductRecord:
        target_bld_no = str(data.get("bld_no") or "").strip()
        if not target_bld_no:
            raise ValueError("BLD NO. 不能为空。")
        with self.unit_of_work_factory() as unit_of_work:
            source = unit_of_work.repository.get(source_product_id)
            if source is None:
                raise ProductNotFoundError(source_product_id)
            if unit_of_work.repository.get_by_bld(target_bld_no) is not None:
                raise ValueError("BLD NO. 已存在，请填写新的产品型号。")
            product = unit_of_work.repository.upsert(data, actor=actor)
            try:
                product = unit_of_work.repository.copy_media_from(
                    source.id,
                    product.id,
                    actor=actor,
                    image_files=image_files,
                    drawing_file=drawing_file,
                )
                unit_of_work.commit()
            except Exception:
                unit_of_work.repository.rollback_copy_media()
                raise
            else:
                unit_of_work.repository.finalize_copy_media()
        self.invalidate_catalog()
        return product

    def save_drawing(self, product_id: int, file: object, *, actor: str) -> ProductRecord:
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.save_drawing(product_id, file, actor=actor)
            unit_of_work.commit()
        self.invalidate_catalog()
        return product

    def deactivate(self, product_id: int, *, actor: str) -> ProductRecord:
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.deactivate(product_id, actor=actor)
            if product is None:
                raise ProductNotFoundError(product_id)
            unit_of_work.commit()
        self.invalidate_catalog()
        return product

    def delete(self, product_id: int, *, actor: str) -> ProductRecord | None:
        with self.unit_of_work_factory() as unit_of_work:
            product = unit_of_work.repository.delete(product_id, actor=actor)
            unit_of_work.commit()
        self.invalidate_catalog()
        return product

    def import_catalog(self, path: Path, *, actor: str) -> int:
        with self.unit_of_work_factory() as unit_of_work:
            imported = unit_of_work.repository.import_catalog(path, actor=actor)
            unit_of_work.commit()
        self.invalidate_catalog()
        return imported

    def catalog_import_choices(self) -> CatalogImportChoices:
        options = self.filter_options(ProductFilters(status="all"))
        return CatalogImportChoices(
            series=tuple(option.value for option in options.brand if option.value),
            items=tuple(option.value for option in options.item if option.value),
        )

    def preview_catalog_import(self, path: Path) -> CatalogImportPreview:
        rows = read_catalog_import(path, choices=self.catalog_import_choices())
        with self.unit_of_work_factory() as unit_of_work:
            products = {
                row.bld_no: product
                for row in rows
                if (product := unit_of_work.repository.get_by_bld(row.bld_no)) is not None
            }
        return build_catalog_import_preview(rows, products)

    def apply_catalog_import(
        self,
        path: Path,
        *,
        expected_digest: str,
        update_bld_nos: set[str],
        actor: str,
    ) -> CatalogImportResult:
        if self.catalog_import_storage is None:
            raise RuntimeError("产品目录导入存储尚未配置。")
        preview = self.preview_catalog_import(path)
        if preview.digest != expected_digest:
            raise CatalogImportPreviewChangedError()
        update_candidates = {conflict.row.bld_no for conflict in preview.conflicts}
        unknown_updates = update_bld_nos - update_candidates
        if unknown_updates:
            raise ValueError("导入确认包含无效的冲突条目，请重新预览。")
        selected_rows = [*preview.new_rows]
        selected_rows.extend(conflict.row for conflict in preview.conflicts if conflict.row.bld_no in update_bld_nos)
        existing_bld_nos = {conflict.row.bld_no: conflict.product.bld_no for conflict in preview.conflicts}
        storage = self.catalog_import_storage
        transaction = CatalogImportFileTransaction(
            catalog_path=storage.catalog_path,
            image_dir=storage.image_dir,
            thumb_dir=storage.thumb_dir,
        )
        source_path = storage.catalog_path.parent / f".catalog-import-{uuid4().hex}.xlsx"
        try:
            with self.unit_of_work_factory() as unit_of_work:
                image_paths = transaction.apply_images(selected_rows)
                for row in selected_rows:
                    data = row.values()
                    data["bld_no"] = existing_bld_nos.get(row.bld_no, row.bld_no)
                    if row.bld_no in image_paths:
                        data["image_path"] = image_paths[row.bld_no]
                    unit_of_work.repository.upsert(data, actor=actor)
                unit_of_work.repository.export_catalog_source(source_path)
                transaction.apply_catalog(source_path)
                unit_of_work.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            source_path.unlink(missing_ok=True)
        transaction.finalize()
        self.invalidate_catalog()
        return CatalogImportResult(
            created_count=len(preview.new_rows),
            updated_count=len(update_bld_nos),
            kept_count=len(preview.conflicts) - len(update_bld_nos) + len(preview.unchanged_rows),
        )

    def export_catalog(
        self,
        path: Path,
        *,
        filters: Mapping[str, object] | ProductFilters,
        export_format: str,
        actor: str,
    ) -> int:
        self.bootstrap_port()
        normalized = build_product_filters(filters)
        try:
            with self.unit_of_work_factory() as unit_of_work:
                exported = unit_of_work.repository.export_catalog(
                    path,
                    filters=normalized,
                    export_format=export_format,
                    actor=actor,
                )
                if exported:
                    unit_of_work.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return exported

    def update_price(
        self,
        product_id: int,
        *,
        price_cny: float,
        expected_updated_at: str,
        actor: str,
    ) -> ProductRecord:
        self.bootstrap_port()
        with self.unit_of_work_factory() as unit_of_work:
            current = unit_of_work.repository.get(product_id)
            if current is None:
                raise ProductNotFoundError(product_id)
            product = unit_of_work.repository.update_price(
                product_id,
                price_cny=price_cny,
                expected_updated_at=expected_updated_at,
                actor=actor,
            )
            if product is None:
                latest = unit_of_work.repository.get(product_id)
                if latest is None:
                    raise ProductNotFoundError(product_id)
                raise ProductVersionConflictError(product_id, latest.updated_at)
            unit_of_work.commit()
        self.invalidate_catalog()
        return product

    def preview_brand_normalization(self) -> BrandNormalizationPreview:
        self.bootstrap_port()
        with self.unit_of_work_factory() as unit_of_work:
            changes = unit_of_work.repository.preview_brand_normalization()
        return build_brand_normalization_preview(changes)

    def normalize_brands(
        self,
        *,
        backup_path: Path,
        expected_digest: str,
        actor: str,
    ) -> BrandNormalizationResult:
        if len(expected_digest) != 64:
            raise ValueError("品牌清洗预览标记无效，请重新预览。")
        if backup_path.exists():
            raise ValueError("品牌清洗备份文件已存在，请重新预览后再试。")
        with self.unit_of_work_factory() as unit_of_work:
            initial_preview = build_brand_normalization_preview(
                unit_of_work.repository.preview_brand_normalization()
            )
            if initial_preview.digest != expected_digest:
                raise BrandNormalizationPreviewChangedError(expected_digest, initial_preview.digest)
            unit_of_work.repository.lock_brand_normalization()
            locked_preview = build_brand_normalization_preview(
                unit_of_work.repository.preview_brand_normalization()
            )
            if locked_preview.digest != expected_digest:
                raise BrandNormalizationPreviewChangedError(expected_digest, locked_preview.digest)
            unit_of_work.repository.backup_database(backup_path)
            changed_count = unit_of_work.repository.apply_brand_normalization(
                list(locked_preview.changes),
                actor=actor,
            )
            unit_of_work.commit()
        self.invalidate_catalog()
        return BrandNormalizationResult(changed_count=changed_count, backup_path=backup_path)
