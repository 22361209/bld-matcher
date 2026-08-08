from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from app.catalog_export import export_products_xlsx
from app.modules.products.persistence import import_catalog
from app.platform.clock import now_text

from .brand_normalization import (
    BrandNormalizationChange,
    BrandNormalizationConflictError,
    canonicalize_brands,
)
from .domain import ProductFilters
from .repository_context import ProductRepositoryContext


class ProductCatalogRepositoryMixin(ProductRepositoryContext):
    def export_catalog_source(self, path: Path) -> None:
        export_products_xlsx(self.connection, path, export_format="bld")

    def import_catalog(self, path: Path, *, actor: str) -> int:
        return import_catalog(self.connection, path, replace=False, actor=actor, commit=False)

    def export_catalog(
        self,
        path: Path,
        *,
        filters: ProductFilters,
        export_format: str,
        actor: str,
        include_price: bool = True,
    ) -> int:
        rows = self._rows(
            filters,
            limit=None,
            sort_by="series" if export_format == "brand" else "bld",
        )
        if not rows:
            return 0
        export_products_xlsx(
            self.connection,
            path,
            export_format=export_format,
            product_rows=rows,
            include_price=include_price,
        )
        status_label = {
            "active": "仅启用产品",
            "inactive": "仅停用产品",
            "all": "包含启用和停用产品",
        }[filters.status]
        self._log_event(
            self.connection,
            "导出目录",
            "catalog",
            path.name,
            ("按汽车品牌格式；" if export_format == "brand" else "按 BLD 号格式；")
            + f"{status_label}；按当前筛选导出 {len(rows)} 条",
            actor=actor,
        )
        return len(rows)

    def preview_brand_normalization(self) -> list[BrandNormalizationChange]:
        changes: list[BrandNormalizationChange] = []
        rows = self.connection.execute(
            "SELECT id, bld_no, series FROM products ORDER BY bld_no COLLATE BLD_NATURAL"
        ).fetchall()
        for row in rows:
            before = str(row["series"] or "")
            after = canonicalize_brands(before)
            if before == after:
                continue
            changes.append(
                BrandNormalizationChange(
                    product_id=int(row["id"]),
                    bld_no=str(row["bld_no"] or ""),
                    before=before,
                    after=after,
                )
            )
        return changes

    def apply_brand_normalization(
        self,
        changes: list[BrandNormalizationChange],
        *,
        actor: str,
    ) -> int:
        timestamp = now_text()
        for change in changes:
            cursor = self.connection.execute(
                """
                UPDATE products
                SET series = ?, updated_at = ?
                WHERE id = ? AND bld_no = ? AND COALESCE(series, '') = ?
                """,
                (change.after, timestamp, change.product_id, change.bld_no, change.before),
            )
            if cursor.rowcount != 1:
                raise BrandNormalizationConflictError(
                    f"产品 {change.bld_no} 的品牌已在预览后发生变化，整批清洗已取消。"
                )
            self._log_event(
                self.connection,
                "清洗产品品牌",
                "product",
                change.bld_no,
                f"品牌: {change.before or '(空)'} -> {change.after or '(空)'}",
                actor=actor,
            )
        self._log_event(
            self.connection,
            "批量清洗产品品牌",
            "catalog",
            "product-brands",
            f"规范 {len(changes)} 条产品品牌；全部转为大写，RAM 归入 DODGE。",
            actor=actor,
        )
        return len(changes)

    def backup_database(self, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.tmp")
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            try:
                source = sqlite3.connect(self.database_path)
                target = sqlite3.connect(temporary_path)
                # The caller holds BEGIN IMMEDIATE on ``self.connection``. A
                # separate source connection can still read the locked snapshot;
                # backing up from the lock-owning connection would self-block.
                source.backup(target)
                target.commit()
                integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity != "ok":
                    raise RuntimeError("产品品牌清洗备份完整性检查失败。")
            finally:
                if target is not None:
                    target.close()
                if source is not None:
                    source.close()
            temporary_path.replace(target_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def lock_brand_normalization(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
