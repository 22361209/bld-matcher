from __future__ import annotations

import sqlite3

from app.bld_sort import bld_sort_key
from app.matcher import compact_text
from app.modules.products.persistence import (
    count_products,
    list_products,
    product_filter_option_rows,
    product_stats,
    rows_for_catalog,
)
from app.product_status import canonical_product_status, format_product_status

from .domain import (
    ProductFilterOption,
    ProductFilterOptions,
    ProductFilters,
    ProductRecord,
    ProductStats,
    compact,
)
from .repository_context import ProductRepositoryContext


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
    source_rank: tuple | None = None,
    source_ranks: dict[str, tuple] | None = None,
) -> None:
    key = value.casefold()
    current = buckets.get(key)
    use_candidate = current is None
    if current is not None and source_rank is not None and source_ranks is not None:
        current_rank = source_ranks.get(key)
        use_candidate = current_rank is None or source_rank < current_rank
    buckets[key] = ProductFilterOption(
        value=value if use_candidate else current.value,
        label=label if use_candidate else current.label,
        count=(current.count if current else 0) + 1,
    )
    if source_rank is not None and source_ranks is not None and use_candidate:
        source_ranks[key] = source_rank


def _option_values(
    row: sqlite3.Row,
    *,
    field: str,
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    if field == "brand":
        seen_tokens: set[str] = set()
        series = str(row["series"] or "")
        for line in series.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            token = compact(line)
            key = token.casefold()
            if not token or key in seen_tokens:
                continue
            seen_tokens.add(key)
            values.append((token, token))
    elif field == "item":
        item = str(row["item"] or "").strip()
        if item:
            values.append((item, item))
    else:
        status = canonical_product_status(row["product_status"])
        if status:
            values.append((status, format_product_status(status, "en", multiline=False)))
    return values or [("", "（空白）")]


def _finalize_options(
    buckets: dict[str, ProductFilterOption],
    *,
    field: str,
    selected: tuple[str, ...],
    blank_selected: bool,
) -> tuple[ProductFilterOption, ...]:
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


class ProductQueryRepositoryMixin(ProductRepositoryContext):
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
        rows = product_filter_option_rows(self.connection, filters)
        buckets: dict[str, dict[str, ProductFilterOption]] = {
            "brand": {},
            "item": {},
            "product_status": {},
        }
        source_ranks: dict[str, dict[str, tuple]] = {
            "brand": {},
            "item": {},
            "product_status": {},
        }
        include_columns = {
            "brand": "include_brand",
            "item": "include_item",
            "product_status": "include_product_status",
        }
        for row in rows:
            source_rank = bld_sort_key(row["bld_no"])
            for field, include_column in include_columns.items():
                if not row[include_column]:
                    continue
                for value, label in _option_values(row, field=field):
                    _add_option_count(
                        buckets[field],
                        value=value,
                        label=label,
                        source_rank=source_rank,
                        source_ranks=source_ranks[field],
                    )
        return ProductFilterOptions(
            brand=_finalize_options(
                buckets["brand"],
                field="brand",
                selected=filters.brands,
                blank_selected=filters.brand_blank,
            ),
            item=_finalize_options(
                buckets["item"],
                field="item",
                selected=filters.items,
                blank_selected=filters.item_blank,
            ),
            product_status=_finalize_options(
                buckets["product_status"],
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

    def stats(self) -> ProductStats:
        return ProductStats(**product_stats(self.connection))

    def catalog_version(self) -> tuple[object, ...]:
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
        return (
            int(product_version[0] or 0),
            str(product_version[1] or ""),
            int(alias_version[0] or 0),
            str(alias_version[1] or ""),
            *file_signatures,
        )

    def catalog_snapshot(self) -> tuple[tuple[object, ...], list[dict], dict[str, str]]:
        products, aliases = rows_for_catalog(self.connection)
        return self.catalog_version(), products, aliases
