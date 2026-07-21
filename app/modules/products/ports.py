from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Mapping, Protocol, Self

from app.matcher import ProductCatalog

from .brand_normalization import BrandNormalizationChange
from .domain import ProductFilterOptions, ProductFilters, ProductRecord, ProductStats


class ProductRepository(Protocol):
    def list(self, filters: ProductFilters, *, limit: int, offset: int) -> list[ProductRecord]: ...

    def count(self, filters: ProductFilters) -> int: ...

    def filter_options(self, filters: ProductFilters) -> ProductFilterOptions: ...

    def get(self, product_id: int) -> ProductRecord | None: ...

    def get_by_bld(self, bld_no: str) -> ProductRecord | None: ...

    def export_catalog_source(self, path: Path) -> None: ...

    def stats(self) -> ProductStats: ...

    def catalog_version(self) -> tuple[object, ...]: ...

    def catalog_snapshot(self) -> tuple[tuple[object, ...], list[dict], dict[str, str]]: ...

    def upsert(self, data: Mapping[str, object], *, actor: str) -> ProductRecord: ...

    def copy_media_from(
        self,
        source_product_id: int,
        target_product_id: int,
        *,
        actor: str,
        image_files: list[tuple[int, object]] | None = None,
        drawing_file: object | None = None,
    ) -> ProductRecord: ...

    def finalize_copy_media(self) -> None: ...

    def rollback_copy_media(self) -> None: ...

    def save_image(self, product_id: int, file: object, *, slot: int, actor: str) -> ProductRecord: ...

    def save_drawing(self, product_id: int, file: object, *, actor: str) -> ProductRecord: ...

    def deactivate(self, product_id: int, *, actor: str) -> ProductRecord | None: ...

    def delete(self, product_id: int, *, actor: str) -> ProductRecord | None: ...

    def update_price(
        self,
        product_id: int,
        *,
        price_cny: float,
        expected_updated_at: str,
        actor: str,
    ) -> ProductRecord | None: ...

    def import_catalog(self, path: Path, *, actor: str) -> int: ...

    def export_catalog(
        self,
        path: Path,
        *,
        filters: ProductFilters,
        export_format: str,
        actor: str,
    ) -> int: ...

    def preview_brand_normalization(self) -> list[BrandNormalizationChange]: ...

    def apply_brand_normalization(
        self,
        changes: list[BrandNormalizationChange],
        *,
        actor: str,
    ) -> int: ...

    def backup_database(self, target_path: Path) -> None: ...

    def lock_brand_normalization(self) -> None: ...


class ProductUnitOfWork(Protocol):
    repository: ProductRepository

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...

    def commit(self) -> None: ...


ProductUnitOfWorkFactory = Callable[[], ProductUnitOfWork]


class CatalogBootstrapPort(Protocol):
    def __call__(self) -> None: ...


class LegacyAliasPort(Protocol):
    def __call__(self) -> dict[str, str]: ...


class ProductCatalogPort(Protocol):
    def catalog(self) -> ProductCatalog | None: ...
