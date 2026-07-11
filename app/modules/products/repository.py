from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from app.catalog_export import export_products_xlsx
from app.database import connect
from app.modules.products.persistence import (
    count_products,
    import_catalog,
    list_products,
    product_stats,
    rows_for_catalog,
    upsert_product,
)
from app.drawings import save_product_drawing
from app.matcher import compact_text
from app.price_import import parse_price_file
from app.product_media import save_product_image
from app.platform.audit_store import log_event
from app.platform.clock import now_text

from .domain import ProductFilters, ProductRecord, ProductStats


def _record(row: sqlite3.Row | None) -> ProductRecord | None:
    if row is None:
        return None
    keys = set(row.keys())
    return ProductRecord(
        id=int(row["id"]),
        bld_no=str(row["bld_no"] or ""),
        series=str(row["series"] or ""),
        item=str(row["item"] or ""),
        oe_no_1=str(row["oe_no_1"] or ""),
        oe_no_2=str(row["oe_no_2"] or ""),
        models=str(row["models"] or ""),
        price_cny=float(row["price_cny"]) if row["price_cny"] is not None else None,
        product_status=str(row["product_status"] or "") if "product_status" in keys else "",
        image_path=str(row["image_path"] or ""),
        image_path_2=str(row["image_path_2"] or "") if "image_path_2" in keys else "",
        image_path_3=str(row["image_path_3"] or "") if "image_path_3" in keys else "",
        image_path_4=str(row["image_path_4"] or "") if "image_path_4" in keys else "",
        image_path_5=str(row["image_path_5"] or "") if "image_path_5" in keys else "",
        drawing_path=str(row["drawing_path"] or "") if "drawing_path" in keys else "",
        drawing_original_name=str(row["drawing_original_name"] or "") if "drawing_original_name" in keys else "",
        drawing_updated_at=str(row["drawing_updated_at"] or "") if "drawing_updated_at" in keys else "",
        active=bool(row["active"]),
        source=str(row["source"] or ""),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


class SQLiteProductRepository:
    def __init__(self, connection: sqlite3.Connection, database_path: Path) -> None:
        self.connection = connection
        self.database_path = database_path

    def list(self, filters: ProductFilters, *, limit: int, offset: int) -> list[ProductRecord]:
        rows = list_products(
            self.connection,
            query=filters.query,
            bld_query=filters.bld_query,
            oe_query=filters.oe_query,
            series_query=filters.series_query,
            model_query=filters.model_query,
            include_inactive=filters.include_inactive,
            only_inactive=filters.only_inactive,
            limit=max(1, min(500, int(limit))),
            offset=max(0, int(offset)),
        )
        return [_record(row) for row in rows if row is not None]

    def count(self, filters: ProductFilters) -> int:
        return count_products(
            self.connection,
            query=filters.query,
            bld_query=filters.bld_query,
            oe_query=filters.oe_query,
            series_query=filters.series_query,
            model_query=filters.model_query,
            include_inactive=filters.include_inactive,
            only_inactive=filters.only_inactive,
        )

    def get(self, product_id: int) -> ProductRecord | None:
        return _record(self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())

    def get_by_bld(self, bld_no: str) -> ProductRecord | None:
        return _record(
            self.connection.execute(
                "SELECT * FROM products WHERE UPPER(bld_no) = UPPER(?)",
                (compact_text(bld_no),),
            ).fetchone()
        )

    def stats(self) -> ProductStats:
        values = product_stats(self.connection)
        return ProductStats(**values)

    def catalog_snapshot(self) -> tuple[tuple[object, ...], list[dict], dict[str, str]]:
        products, aliases = rows_for_catalog(self.connection)
        product_version = self.connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM products"
        ).fetchone()
        alias_version = self.connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM aliases WHERE active = 1"
        ).fetchone()
        file_signatures = []
        for path in (
            self.database_path,
            self.database_path.with_name(f"{self.database_path.name}-wal"),
            self.database_path.with_name(f"{self.database_path.name}-shm"),
        ):
            try:
                stat = path.stat()
                file_signatures.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                file_signatures.append((0, 0))
        version = (
            int(product_version[0] or 0),
            str(product_version[1] or ""),
            int(alias_version[0] or 0),
            str(alias_version[1] or ""),
            *file_signatures,
        )
        return version, products, aliases

    def upsert(self, data: Mapping[str, object], *, actor: str) -> ProductRecord:
        upsert_product(
            self.connection,
            dict(data),
            source="web",
            actor=actor,
            commit=False,
            preserve_blank_price=False,
        )
        product = self.get_by_bld(str(data.get("bld_no") or ""))
        if product is None:
            raise RuntimeError("Saved product could not be reloaded.")
        return product

    def save_image(self, product_id: int, file: object, *, slot: int, actor: str) -> ProductRecord:
        row = self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise LookupError("产品不存在。")
        save_product_image(self.connection, row, file, slot=slot, commit=False)
        log_event(
            self.connection,
            "上传产品图片",
            "product",
            str(row["bld_no"]),
            f"图片 {slot}: {getattr(file, 'filename', '') or ''}",
            actor=actor,
        )
        product = self.get(product_id)
        if product is None:
            raise RuntimeError("Product image update could not be reloaded.")
        return product

    def save_drawing(self, product_id: int, file: object, *, actor: str) -> ProductRecord:
        row = self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise LookupError("产品不存在。")
        save_product_drawing(self.connection, row, file, commit=False)
        log_event(
            self.connection,
            "上传图纸",
            "product",
            str(row["bld_no"]),
            str(getattr(file, "filename", "") or ""),
            actor=actor,
        )
        product = self.get(product_id)
        if product is None:
            raise RuntimeError("Product drawing update could not be reloaded.")
        return product

    def deactivate(self, product_id: int, *, actor: str) -> ProductRecord | None:
        product = self.get(product_id)
        if product is None:
            return None
        self.connection.execute(
            "UPDATE products SET active = 0, updated_at = ? WHERE id = ?",
            (now_text(), product_id),
        )
        log_event(
            self.connection,
            "停用产品",
            "product",
            product.bld_no,
            "状态: 启用 -> 停用",
            actor=actor,
        )
        return self.get(product_id)

    def delete(self, product_id: int, *, actor: str) -> ProductRecord | None:
        product = self.get(product_id)
        if product is None:
            return None
        alias_count = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM aliases WHERE bld_no = ? AND active = 1",
                (product.bld_no,),
            ).fetchone()[0]
            or 0
        )
        self.connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
        if alias_count:
            self.connection.execute(
                "UPDATE aliases SET active = 0, updated_at = ? WHERE bld_no = ? AND active = 1",
                (now_text(), product.bld_no),
            )
        detail = f"品牌: {product.series or '(空)'}；产品名称: {product.item or '(空)'}"
        if alias_count:
            detail += f"；同步停用人工映射 {alias_count} 条"
        log_event(self.connection, "删除产品", "product", product.bld_no, detail, actor=actor)
        return product

    def update_prices(self, rows: list[dict], *, actor: str) -> tuple[int, int]:
        updated = 0
        skipped = 0
        for row in rows:
            if row.get("status") != "matched":
                skipped += 1
                continue
            self.connection.execute(
                "UPDATE products SET price_cny = ?, updated_at = ? WHERE bld_no = ?",
                (row["price"], now_text(), row["bld_no"]),
            )
            updated += 1
        log_event(
            self.connection,
            "批量维护单价",
            "product",
            "Unit Price",
            f"更新 {updated} 条，跳过 {skipped} 条",
            actor=actor,
        )
        return updated, skipped

    def import_catalog(self, path: Path, *, actor: str) -> int:
        return import_catalog(self.connection, path, replace=False, actor=actor, commit=False)

    def export_catalog(
        self,
        path: Path,
        *,
        include_inactive: bool,
        export_format: str,
        actor: str,
    ) -> None:
        export_products_xlsx(
            self.connection,
            path,
            include_inactive=include_inactive,
            export_format=export_format,
        )
        log_event(
            self.connection,
            "导出目录",
            "catalog",
            path.name,
            ("按汽车品牌格式；" if export_format == "brand" else "按 BLD 号格式；")
            + ("包含停用产品" if include_inactive else "仅启用产品"),
            actor=actor,
        )

    def preview_prices(self, path: Path) -> dict:
        return parse_price_file(path, self.connection)


ConnectionFactory = Callable[[Path], sqlite3.Connection]


class SQLiteProductUnitOfWork:
    def __init__(self, database_path: Path, *, connection_factory: ConnectionFactory = connect) -> None:
        self.database_path = database_path
        self.connection_factory = connection_factory
        self.connection: sqlite3.Connection | None = None
        self.repository: SQLiteProductRepository
        self._committed = False

    def __enter__(self) -> SQLiteProductUnitOfWork:
        self.connection = self.connection_factory(self.database_path)
        self.repository = SQLiteProductRepository(self.connection, self.database_path)
        self._committed = False
        return self

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("Product unit of work is not active.")
        self.connection.commit()
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.connection is None:
            return
        if exc_type is not None or not self._committed:
            self.connection.rollback()
        self.connection.close()
        self.connection = None
