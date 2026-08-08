from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.matcher import compact_text
from app.modules.products.persistence import rename_bld_no as rename_persisted_bld_no
from app.modules.products.persistence import upsert_product
from app.platform.clock import now_text

from .domain import ProductRecord
from .repository_context import ProductRepositoryContext
from .repository_media_transactions import ProductRenameMediaTransaction


class ProductWriteRepositoryMixin(ProductRepositoryContext):
    _rename_media_transaction: ProductRenameMediaTransaction | None

    def rename_bld_no(
        self,
        old_bld_no: str,
        new_bld_no: str,
        *,
        actor: str,
        backup_path: Path,
    ) -> dict[str, int]:
        old = compact_text(old_bld_no)
        new = compact_text(new_bld_no)
        if not old or not new:
            raise ValueError("BLD NO. 不能为空。")
        if old == new:
            raise ValueError("新旧 BLD NO. 相同，无需迁移。")

        old_row = self.connection.execute(
            "SELECT * FROM products WHERE UPPER(bld_no) = UPPER(?)", (old,)
        ).fetchone()
        if old_row is None:
            raise ValueError(f"产品 {old} 不存在。")
        if self.connection.execute(
            "SELECT 1 FROM products WHERE UPPER(bld_no) = UPPER(?)", (new,)
        ).fetchone():
            raise ValueError(f"BLD NO. {new} 已存在。")

        self.backup_database(backup_path)
        transaction = ProductRenameMediaTransaction(old, new, old_row)
        self._rename_media_transaction = transaction
        try:
            transaction.begin()
            counts = rename_persisted_bld_no(
                self.connection,
                old,
                new,
                actor,
                commit=False,
            )
        except Exception:
            transaction.rollback()
            self._rename_media_transaction = None
            raise
        return counts

    def finalize_rename_media(self) -> None:
        if self._rename_media_transaction is None:
            return
        self._rename_media_transaction.finalize()
        self._rename_media_transaction = None

    def rollback_rename_media(self) -> None:
        if self._rename_media_transaction is None:
            return
        try:
            self._rename_media_transaction.rollback()
        finally:
            self._rename_media_transaction = None

    def upsert(
        self,
        data: Mapping[str, object],
        *,
        actor: str,
        preserve_blank_price: bool = False,
    ) -> ProductRecord:
        upsert_product(
            self.connection,
            dict(data),
            source="web",
            actor=actor,
            commit=False,
            preserve_blank_price=preserve_blank_price,
        )
        product = self.get_by_bld(str(data.get("bld_no") or ""))
        if product is None:
            raise RuntimeError("Saved product could not be reloaded.")
        return product

    def deactivate(self, product_id: int, *, actor: str) -> ProductRecord | None:
        product = self.get(product_id)
        if product is None:
            return None
        self.connection.execute(
            "UPDATE products SET active = 0, updated_at = ? WHERE id = ?",
            (now_text(), product_id),
        )
        self._log_event(
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
        self._log_event(self.connection, "删除产品", "product", product.bld_no, detail, actor=actor)
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
        self._log_event(
            self.connection,
            "API 更新产品单价",
            "product",
            updated.bld_no,
            f"含税单价：{product.price_cny if product.price_cny is not None else '(空)'} -> {price_cny}",
            actor=actor,
        )
        return updated
