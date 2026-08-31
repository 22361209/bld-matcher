from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

from app.config import (
    DATA_DIR,
    DRAWING_ARCHIVE_DIR,
    DRAWING_PDF_DIR,
    PRODUCT_IMAGE_ARCHIVE_DIR,
    PRODUCT_IMAGE_DIR,
)
from app.drawings import drawing_storage_name, product_drawing_path, safe_filename_part
from app.product_media import (
    PRODUCT_IMAGE_DATA_PREFIX,
    PRODUCT_IMAGE_OUTPUT_SUFFIX,
    image_slot_field,
    product_image_storage_name,
    product_image_thumb_path,
    resolve_product_image_path,
)


class NewProductMediaTransaction:
    """Restore any media side effects when creating a copied product fails."""

    def __init__(
        self,
        *,
        source: sqlite3.Row,
        target: sqlite3.Row,
        image_files: list[tuple[int, object]] | None,
        drawing_file: object | None,
    ) -> None:
        self.source = source
        self.target = target
        self.image_files = image_files or []
        self.drawing_file = drawing_file
        self.backup_dir: Path | None = None
        self.file_changes: list[tuple[Path, Path | None]] = []
        self.directory_changes: list[tuple[Path, Path | None]] = []

    @staticmethod
    def _atomic_copy(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.rollback")
        try:
            shutil.copy2(source, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def _image_targets(self) -> set[Path]:
        targets: set[Path] = set()
        target_bld_no = self.target["bld_no"]

        def add_image_target(slot: int) -> None:
            path = PRODUCT_IMAGE_DIR / product_image_storage_name(target_bld_no, PRODUCT_IMAGE_OUTPUT_SUFFIX, slot)
            targets.add(path)
            thumbnail = product_image_thumb_path(path.name)
            if thumbnail is not None:
                targets.add(thumbnail)

        for slot in range(1, 6):
            field = image_slot_field(slot)
            reference = str(self.source[field] or "") if field in self.source.keys() else ""
            source_path = resolve_product_image_path(reference.rsplit("/", 1)[-1])
            if source_path is not None:
                add_image_target(slot)
        for slot, file in self.image_files:
            if Path(str(getattr(file, "filename", "") or "")).suffix:
                add_image_target(slot)
        return targets

    def begin(self) -> None:
        backup_root = DATA_DIR / "local-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        self.backup_dir = Path(tempfile.mkdtemp(prefix="copy-product-media-", dir=backup_root))
        try:
            targets = self._image_targets()
            if product_drawing_path(self.source) is not None or self.drawing_file is not None:
                targets.add(DRAWING_PDF_DIR / drawing_storage_name(self.target["bld_no"]))
            for index, path in enumerate(sorted(targets)):
                backup = self.backup_dir / "files" / str(index) if path.exists() else None
                if backup is not None:
                    self._atomic_copy(path, backup)
                self.file_changes.append((path, backup))

            safe_bld_no = safe_filename_part(self.target["bld_no"], "product")
            for name, path in (
                ("product-images", PRODUCT_IMAGE_ARCHIVE_DIR / safe_bld_no),
                ("drawings", DRAWING_ARCHIVE_DIR / safe_bld_no),
            ):
                backup = self.backup_dir / "archives" / name if path.exists() else None
                if backup is not None:
                    shutil.copytree(path, backup)
                self.directory_changes.append((path, backup))
        except Exception:
            self.finalize()
            raise

    def rollback(self) -> None:
        errors: list[str] = []
        for path, backup in reversed(self.file_changes):
            try:
                if backup is not None and backup.exists():
                    self._atomic_copy(backup, path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                errors.append(path.name)
        for path, backup in reversed(self.directory_changes):
            try:
                if path.exists():
                    shutil.rmtree(path)
                if backup is not None and backup.exists():
                    shutil.copytree(backup, path)
            except OSError:
                errors.append(path.name)
        if errors:
            raise RuntimeError(f"复制产品媒体回滚失败：{', '.join(errors)}")
        self.finalize()

    def finalize(self) -> None:
        if self.backup_dir is not None:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
            self.backup_dir = None


class ProductRenameMediaTransaction:
    """Rename product media immediately and reverse completed moves on failure."""

    def __init__(self, old_bld_no: str, new_bld_no: str, product_row: sqlite3.Row) -> None:
        self.old_bld_no = old_bld_no
        self.new_bld_no = new_bld_no
        self.product_row = product_row
        self.moves: list[tuple[Path, Path]] = []
        self.completed: list[tuple[Path, Path]] = []
        self.rolled_back = False

    def _plan(self) -> list[tuple[Path, Path]]:
        moves: list[tuple[Path, Path]] = []
        old_safe = safe_filename_part(self.old_bld_no, "product")
        new_safe = safe_filename_part(self.new_bld_no, "product")

        for slot in range(1, 6):
            field = image_slot_field(slot)
            reference = str(self.product_row[field] or "") if field in self.product_row.keys() else ""
            if not reference.startswith(PRODUCT_IMAGE_DATA_PREFIX):
                continue
            source = resolve_product_image_path(reference[len(PRODUCT_IMAGE_DATA_PREFIX) :])
            if source is None:
                continue
            target_name = product_image_storage_name(self.new_bld_no, source.suffix, slot)
            target = PRODUCT_IMAGE_DIR / target_name
            moves.append((source, target))
            thumb_source = product_image_thumb_path(source.name)
            if thumb_source is not None and thumb_source.exists():
                thumb_target = product_image_thumb_path(target_name)
                if thumb_target is not None:
                    moves.append((thumb_source, thumb_target))

        source_drawing = product_drawing_path(self.product_row)
        if source_drawing is not None:
            target_drawing = DRAWING_PDF_DIR / drawing_storage_name(self.new_bld_no)
            moves.append((source_drawing, target_drawing))

        old_image_archive = PRODUCT_IMAGE_ARCHIVE_DIR / old_safe
        new_image_archive = PRODUCT_IMAGE_ARCHIVE_DIR / new_safe
        if old_image_archive.exists():
            moves.append((old_image_archive, new_image_archive))

        old_drawing_archive = DRAWING_ARCHIVE_DIR / old_safe
        new_drawing_archive = DRAWING_ARCHIVE_DIR / new_safe
        if old_drawing_archive.exists():
            moves.append((old_drawing_archive, new_drawing_archive))

        return moves

    def begin(self) -> None:
        self.moves = self._plan()
        for source, target in self.moves:
            if target.exists():
                raise FileExistsError(f"目标文件已存在，无法重命名：{target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            self.completed.append((source, target))

    def rollback(self) -> None:
        if self.rolled_back:
            return
        for source, target in reversed(self.completed):
            if target.exists():
                target.replace(source)
        self.rolled_back = True

    def finalize(self) -> None:
        pass
