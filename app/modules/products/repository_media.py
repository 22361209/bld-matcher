from __future__ import annotations

from typing import cast

from werkzeug.datastructures import FileStorage

from app.drawings import delete_product_drawing, product_drawing_path
from app.product_media import delete_product_image, image_slot_field, resolve_product_image_path, save_product_image

from .domain import ProductRecord
from .repository_context import ProductRepositoryContext
from .repository_media_transactions import NewProductMediaTransaction


class ProductMediaRepositoryMixin(ProductRepositoryContext):
    _copy_media_transaction: NewProductMediaTransaction | None

    def copy_media_from(
        self,
        source_product_id: int,
        target_product_id: int,
        *,
        actor: str,
        image_files: list[tuple[int, object]] | None = None,
        drawing_file: object | None = None,
    ) -> ProductRecord:
        source = self.connection.execute("SELECT * FROM products WHERE id = ?", (source_product_id,)).fetchone()
        target = self.connection.execute("SELECT * FROM products WHERE id = ?", (target_product_id,)).fetchone()
        if source is None or target is None:
            raise LookupError("复制来源或新建产品不存在。")

        if self._copy_media_transaction is not None:
            raise RuntimeError("复制产品媒体事务尚未完成。")
        transaction = NewProductMediaTransaction(
            source=source,
            target=target,
            image_files=image_files,
            drawing_file=drawing_file,
        )
        transaction.begin()
        self._copy_media_transaction = transaction
        try:
            for slot in range(1, 6):
                field = image_slot_field(slot)
                reference = str(source[field] or "") if field in source.keys() else ""
                source_path = resolve_product_image_path(reference.rsplit("/", 1)[-1])
                if source_path is None:
                    if reference:
                        raise ValueError(f"来源产品图片 {slot} 文件未找到，无法复制。")
                    continue
                with source_path.open("rb") as handle:
                    save_product_image(
                        self.connection,
                        target,
                        FileStorage(stream=handle, filename=source_path.name),
                        slot=slot,
                        commit=False,
                    )

            source_drawing = product_drawing_path(source)
            drawing_reference = str(source["drawing_path"] or "") if "drawing_path" in source.keys() else ""
            if drawing_reference and source_drawing is None:
                raise ValueError("来源产品图纸文件未找到，无法复制。")
            if source_drawing is not None:
                original_name = str(source["drawing_original_name"] or "") or source_drawing.name
                with source_drawing.open("rb") as handle:
                    self._save_product_drawing(
                        self.connection,
                        target,
                        FileStorage(stream=handle, filename=original_name),
                        commit=False,
                    )
            for slot, file in image_files or []:
                self.save_image(target_product_id, file, slot=slot, actor=actor)
            if drawing_file is not None:
                self.save_drawing(target_product_id, drawing_file, actor=actor)
        except Exception:
            try:
                transaction.rollback()
            finally:
                self._copy_media_transaction = None
            raise

        self._log_event(
            self.connection,
            "复制产品资料",
            "product",
            str(target["bld_no"]),
            f"从 {source['bld_no']} 复制文字资料、图片和图纸。",
            actor=actor,
        )
        product = self.get(target_product_id)
        if product is None:
            raise RuntimeError("Copied product could not be reloaded.")
        return product

    def finalize_copy_media(self) -> None:
        if self._copy_media_transaction is None:
            return
        self._copy_media_transaction.finalize()
        self._copy_media_transaction = None

    def rollback_copy_media(self) -> None:
        if self._copy_media_transaction is None:
            return
        try:
            self._copy_media_transaction.rollback()
        finally:
            self._copy_media_transaction = None

    def save_image(self, product_id: int, file: object, *, slot: int, actor: str) -> ProductRecord:
        row = self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise LookupError("产品不存在。")
        upload = cast(FileStorage, file)
        save_product_image(self.connection, row, upload, slot=slot, commit=False)
        self._log_event(
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
        self._save_product_drawing(self.connection, row, upload, commit=False)
        self._log_event(
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

    def delete_image(self, product_id: int, slot: int, *, actor: str) -> ProductRecord:
        row = self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise LookupError("产品不存在。")
        archived_path = delete_product_image(self.connection, row, slot, commit=False)
        self._log_event(
            self.connection,
            "删除产品图片",
            "product",
            str(row["bld_no"]),
            f"图片 {slot}" + (f": {archived_path.name}" if archived_path else ""),
            actor=actor,
        )
        product = self.get(product_id)
        if product is None:
            raise RuntimeError("Product image delete could not be reloaded.")
        return product

    def delete_drawing(self, product_id: int, *, actor: str) -> ProductRecord:
        row = self.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise LookupError("产品不存在。")
        archived_path = delete_product_drawing(self.connection, row, commit=False)
        self._log_event(
            self.connection,
            "删除图纸",
            "product",
            str(row["bld_no"]),
            archived_path.name if archived_path else "",
            actor=actor,
        )
        product = self.get(product_id)
        if product is None:
            raise RuntimeError("Product drawing delete could not be reloaded.")
        return product
