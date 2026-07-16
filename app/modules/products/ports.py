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

    def catalog_snapshot(self) -> tuple[tuple[object, ...], list[dict], dict[str, str]]: ...

    def upsert(self, data: Mapping[str, object], *, actor: str) -> ProductRecord: ...

    def save_image(self, product_id: int, file: object, *, slot: int, actor: str) -> ProductRecord: ...

    def save_drawing(self, product_id: int, file: object, *, actor: str) -> ProductRecord: ...

    def deactivate(self, product_id: int, *, actor: str) -> ProductRecord | None: ...

    def delete(self, product_id: int, *, actor: str) -> ProductRecord | None: ...

    def update_prices(self, rows: list[dict], *, actor: str) -> tuple[int, int]: ...

    def import_catalog(self, path: Path, *, actor: str) -> int: ...

    def export_catalog(
        self,
        path: Path,
        *,
        filters: ProductFilters,
        export_format: str,
        actor: str,
    ) -> int: ...

    def preview_prices(self, path: Path) -> dict: ...

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
