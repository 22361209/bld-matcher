from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import cast
from uuid import uuid4

from werkzeug.datastructures import FileStorage

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
from app.product_media import save_product_image
from app.platform.audit_store import log_event
from app.platform.clock import now_text
from app.product_status import canonical_product_status, format_product_status

from .brand_normalization import (
    BrandNormalizationChange,
    BrandNormalizationConflictError,
    canonicalize_brands,
)
from .domain import (
    ProductFilterOption,
    ProductFilterOptions,
    ProductFilters,
    ProductRecord,
    ProductStats,
    compact,
)


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


def _add_option_count(
    buckets: dict[str, ProductFilterOption],
    *,
    value: str,
    label: str,
) -> None:
    key = value.casefold()
    current = buckets.get(key)
    buckets[key] = ProductFilterOption(
        value=current.value if current else value,
        label=current.label if current else label,
        count=(current.count if current else 0) + 1,
    )


def _options_from_records(
    records: list[ProductRecord],
    *,
    field: str,
    selected: tuple[str, ...],
    blank_selected: bool,
) -> tuple[ProductFilterOption, ...]:
    buckets: dict[str, ProductFilterOption] = {}
    for record in records:
        values: list[tuple[str, str]] = []
        if field == "brand":
            seen_tokens: set[str] = set()
            for line in record.series.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                token = compact(line)
                key = token.casefold()
                if not token or key in seen_tokens:
                    continue
                seen_tokens.add(key)
                values.append((token, token))
        elif field == "item":
            item = record.item.strip()
            if item:
                values.append((item, item))
        else:
            status = canonical_product_status(record.product_status)
            if status:
                values.append((status, format_product_status(status, "en", multiline=False)))

        if not values:
            values.append(("", "（空白）"))
        for value, label in values:
            _add_option_count(buckets, value=value, label=label)

    selected_values = (*selected, "") if blank_selected else selected
    for value in selected_values:
        key = value.casefold()
        current = buckets.get(key)
        if current is not None:
            buckets[key] = ProductFilterOption(value=value, label=current.label, count=current.count)
            continue
        if not value:
            label = "（空白）"
        elif field == "product_status":
            label = format_product_status(value, "en", multiline=False)
        else:
            label = value
        buckets[key] = ProductFilterOption(value=value, label=label, count=0)

    options = list(buckets.values())
    return tuple(
        sorted(
            options,
            key=lambda option: (
                not option.value,
                option.label.casefold(),
                option.value.casefold(),
            ),
        )
    )


class SQLiteProductRepository:
    def __init__(self, connection: sqlite3.Connection, database_path: Path) -> None:
        self.connection = connection
        self.database_path = database_path
        self.connection.create_function(
            "PRODUCT_STATUS_KEY",
            1,
            canonical_product_status,
            deterministic=True,
        )

    def _rows(
        self,
        filters: ProductFilters,
        *,
        limit: int | None,
        offset: int = 0,
        sort_by: str = "bld",
    ) -> list[sqlite3.Row]:
        return list_products(
            self.connection,
            query=filters.query,
            bld_query=filters.bld_query,
            oe_query=filters.oe_query,
            series_query=filters.series_query,
            model_query=filters.model_query,
            include_inactive=filters.include_inactive,
            only_inactive=filters.only_inactive,
            brands=filters.brands,
            items=filters.items,
            product_statuses=filters.product_statuses,
            brand_blank=filters.brand_blank,
            item_blank=filters.item_blank,
            product_status_blank=filters.product_status_blank,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )

    def list(self, filters: ProductFilters, *, limit: int, offset: int) -> list[ProductRecord]:
        rows = self._rows(
            filters,
            limit=max(1, min(500, int(limit))),
            offset=max(0, int(offset)),
        )
        return [record for row in rows if (record := _record(row)) is not None]

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
            brands=filters.brands,
            items=filters.items,
            product_statuses=filters.product_statuses,
            brand_blank=filters.brand_blank,
            item_blank=filters.item_blank,
            product_status_blank=filters.product_status_blank,
        )

    def filter_options(self, filters: ProductFilters) -> ProductFilterOptions:
        brand_records = [
            record
            for row in self._rows(replace(filters, brands=(), brand_blank=False), limit=None)
            if (record := _record(row)) is not None
        ]
        item_records = [
            record
            for row in self._rows(replace(filters, items=(), item_blank=False), limit=None)
            if (record := _record(row)) is not None
        ]
        status_records = [
            record
            for row in self._rows(
                replace(filters, product_statuses=(), product_status_blank=False),
                limit=None,
            )
            if (record := _record(row)) is not None
        ]
        return ProductFilterOptions(
            brand=_options_from_records(
                brand_records,
                field="brand",
                selected=filters.brands,
                blank_selected=filters.brand_blank,
            ),
            item=_options_from_records(
                item_records,
                field="item",
                selected=filters.items,
                blank_selected=filters.item_blank,
            ),
            product_status=_options_from_records(
                status_records,
                field="product_status",
                selected=filters.product_statuses,
                blank_selected=filters.product_status_blank,
            ),
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

    def export_catalog_source(self, path: Path) -> None:
        export_products_xlsx(self.connection, path, export_format="bld")

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
        upload = cast(FileStorage, file)
        save_product_image(self.connection, row, upload, slot=slot, commit=False)
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
        upload = cast(FileStorage, file)
        save_product_drawing(self.connection, row, upload, commit=False)
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

    def update_price(
        self,
        product_id: int,
        *,
        price_cny: float,
        expected_updated_at: str,
        actor: str,
    ) -> ProductRecord | None:
        product = self.get(product_id)
        if product is None:
            return None
        timestamp = now_text()
        cursor = self.connection.execute(
            """
            UPDATE products
               SET price_cny = ?, updated_at = ?
             WHERE id = ? AND updated_at = ?
            """,
            (price_cny, timestamp, product_id, expected_updated_at),
        )
        if cursor.rowcount != 1:
            return None
        updated = self.get(product_id)
        if updated is None:
            raise RuntimeError("Product price update could not be reloaded.")
        log_event(
            self.connection,
            "API 更新产品单价",
            "product",
            updated.bld_no,
            f"含税单价：{product.price_cny if product.price_cny is not None else '(空)'} -> {price_cny}",
            actor=actor,
        )
        return updated

    def import_catalog(self, path: Path, *, actor: str) -> int:
        return import_catalog(self.connection, path, replace=False, actor=actor, commit=False)

    def export_catalog(
        self,
        path: Path,
        *,
        filters: ProductFilters,
        export_format: str,
        actor: str,
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
        )
        status_label = {
            "active": "仅启用产品",
            "inactive": "仅停用产品",
            "all": "包含启用和停用产品",
        }[filters.status]
        log_event(
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
            log_event(
                self.connection,
                "清洗产品品牌",
                "product",
                change.bld_no,
                f"品牌: {change.before or '(空)'} -> {change.after or '(空)'}",
                actor=actor,
            )
        log_event(
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
