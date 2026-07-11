from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from threading import Lock

from app.matcher import ProductCatalog

from .domain import ProductFilters, ProductPage, ProductRecord, ProductStats, build_product_filters
from .ports import CatalogBootstrapPort, LegacyAliasPort, ProductUnitOfWorkFactory


class ProductNotFoundError(LookupError):
    def __init__(self, product_id: int) -> None:
        super().__init__(f"产品 {product_id} 不存在。")
        self.product_id = product_id


class ProductService:
    def __init__(
        self,
        unit_of_work_factory: ProductUnitOfWorkFactory,
        bootstrap_port: CatalogBootstrapPort,
        legacy_alias_port: LegacyAliasPort,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.bootstrap_port = bootstrap_port
        self.legacy_alias_port = legacy_alias_port
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

    def stats(self) -> ProductStats:
        self.bootstrap_port()
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.stats()

    def catalog(self) -> ProductCatalog | None:
        self.bootstrap_port()
        with self.unit_of_work_factory() as unit_of_work:
            version, rows, aliases = unit_of_work.repository.catalog_snapshot()
        legacy_aliases = self.legacy_alias_port()
        version = (*version, tuple(sorted(legacy_aliases.items())))
        with self._catalog_lock:
            if version == self._catalog_version:
                return self._catalog
            aliases.update(legacy_aliases)
            self._catalog = ProductCatalog(rows, manual_map=aliases) if rows else None
            self._catalog_version = version
            return self._catalog

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

    def export_catalog(
        self,
        path: Path,
        *,
        include_inactive: bool,
        export_format: str,
        actor: str,
    ) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.export_catalog(
                path,
                include_inactive=include_inactive,
                export_format=export_format,
                actor=actor,
            )
            unit_of_work.commit()

    def preview_prices(self, path: Path) -> dict:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.preview_prices(path)

    def apply_prices(self, rows: list[dict], *, actor: str) -> tuple[int, int]:
        with self.unit_of_work_factory() as unit_of_work:
            result = unit_of_work.repository.update_prices(rows, actor=actor)
            unit_of_work.commit()
        self.invalidate_catalog()
        return result
