from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from .config import PRODUCT_IMAGE_ARCHIVE_DIR, PRODUCT_IMAGE_DATA_PREFIX, PRODUCT_IMAGE_DIR, PRODUCT_IMAGE_THUMB_DIR
from .drawings import safe_filename_part
from .platform.clock import now_text
from .product_image_processing import (
    PRODUCT_IMAGE_INPUT_MAX_BYTES,
    PRODUCT_IMAGE_MAX_PIXELS,
    PRODUCT_IMAGE_OUTPUT_SUFFIX,
    atomic_write_bytes,
    process_product_image,
    process_product_thumbnail,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_SLOT_FIELDS = ("image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5")


def image_slot_field(slot: int) -> str:
    if not 1 <= slot <= len(IMAGE_SLOT_FIELDS):
        raise ValueError("产品图片位置必须在 1 到 5 之间。")
    return IMAGE_SLOT_FIELDS[slot - 1]


def product_image_storage_name(bld_no: object, suffix: str, slot: int = 1) -> str:
    return f"{safe_filename_part(bld_no, 'product')}_{slot}{suffix.lower()}"


def product_image_storage_candidates(bld_no: object, suffix: str, slot: int = 1) -> tuple[str, ...]:
    safe_bld = safe_filename_part(bld_no, "product")
    normalized_suffix = suffix.lower()
    canonical = product_image_storage_name(safe_bld, normalized_suffix, slot)
    legacy_suffixes = ("", "-1") if slot == 1 else (f"-{slot}",)
    return (canonical, *(f"{safe_bld}{slot_suffix}{normalized_suffix}" for slot_suffix in legacy_suffixes))


def _header_matches_image_suffix(header: bytes, suffix: str) -> bool:
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def _rewind_file(file: FileStorage) -> None:
    try:
        file.stream.seek(0)
    except (AttributeError, OSError):
        pass


def validate_product_image_file(file: FileStorage) -> None:
    original_name = Path(file.filename or "").name.strip()
    if not original_name:
        raise ValueError("请选择产品图片文件。")
    suffix = Path(original_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("产品图片支持 JPG、PNG、WEBP。")
    try:
        file.stream.seek(0, 2)
        size = file.stream.tell()
        file.stream.seek(0)
        header = file.stream.read(16)
    finally:
        _rewind_file(file)
    if not header:
        raise ValueError("产品图片文件为空。")
    if not _header_matches_image_suffix(header, suffix):
        raise ValueError("文件内容不是支持的图片格式。")
    if size > PRODUCT_IMAGE_INPUT_MAX_BYTES:
        raise ValueError("单张产品图片源文件不能超过 30 MB。")
    try:
        with Image.open(file.stream) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > PRODUCT_IMAGE_MAX_PIXELS:
                raise ValueError("产品图片总像素不能超过 5000 万。")
            if int(getattr(opened, "n_frames", 1) or 1) > 1:
                raise ValueError("产品图片不支持动画格式。")
            opened.verify()
    except ValueError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError) as exc:
        raise ValueError("文件内容不是有效的产品图片。") from exc
    finally:
        _rewind_file(file)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _safe_direct_image_name(name: str) -> str:
    return Path(name or "").name


def product_image_thumb_path(name: str) -> Path | None:
    safe_name = _safe_direct_image_name(name)
    if not safe_name:
        return None
    path = (PRODUCT_IMAGE_THUMB_DIR / f"{Path(safe_name).stem}{PRODUCT_IMAGE_OUTPUT_SUFFIX}").resolve()
    root = PRODUCT_IMAGE_THUMB_DIR.resolve()
    if root != path.parent:
        return None
    return path


def generate_product_image_thumb(source: Path) -> Path | None:
    destination = product_image_thumb_path(source.name)
    if destination is None:
        return None
    PRODUCT_IMAGE_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    try:
        payload, _size = process_product_thumbnail(source)
        atomic_write_bytes(destination, payload)
        return destination
    except (OSError, ValueError):
        return None


def resolve_product_image_path(name: str) -> Path | None:
    safe_name = _safe_direct_image_name(name)
    if not safe_name:
        return None
    path = (PRODUCT_IMAGE_DIR / safe_name).resolve()
    root = PRODUCT_IMAGE_DIR.resolve()
    if root != path.parent:
        return None
    return path if path.exists() and path.is_file() else None


def resolve_product_image_thumb_path(name: str) -> Path | None:
    source = resolve_product_image_path(name)
    if not source:
        return None
    destination = product_image_thumb_path(source.name)
    if destination is None:
        return None
    try:
        thumb_is_current = destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        thumb_is_current = False
    if thumb_is_current:
        return destination
    return generate_product_image_thumb(source)


def _fallback_product_image_path(bld_no: object, slot: int) -> Path | None:
    for suffix in (PRODUCT_IMAGE_OUTPUT_SUFFIX, ".jpg", ".jpeg", ".png"):
        for name in product_image_storage_candidates(bld_no, suffix, slot):
            candidate = resolve_product_image_path(name)
            if candidate is not None:
                return candidate
    return None


def _archive_path(product_bld_no: object, source: Path) -> Path:
    archive_dir = PRODUCT_IMAGE_ARCHIVE_DIR / safe_filename_part(product_bld_no, "product")
    archive_dir.mkdir(parents=True, exist_ok=True)
    return _unique_path(archive_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{source.name}")


def save_product_image(conn: sqlite3.Connection, product: sqlite3.Row, file: FileStorage, slot: int = 1, *, commit: bool = True) -> Path:
    field = image_slot_field(slot)
    original_name = Path(file.filename or "").name.strip()
    if not original_name:
        raise ValueError("请选择产品图片文件。")
    if Path(original_name).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("产品图片支持 JPG、PNG、WEBP。")
    processed = process_product_image(file.stream)

    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_IMAGE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    destination = PRODUCT_IMAGE_DIR / product_image_storage_name(
        product["bld_no"], PRODUCT_IMAGE_OUTPUT_SUFFIX, slot
    )
    destination_thumb = product_image_thumb_path(destination.name)
    if destination_thumb is None:
        raise ValueError("产品图片缩略图路径无效。")
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.uploading")
    temporary_thumb = destination_thumb.with_name(f".{destination_thumb.name}.{uuid4().hex}.uploading")
    atomic_write_bytes(temporary, processed.large)
    atomic_write_bytes(temporary_thumb, processed.thumbnail)

    image_path = product[field] if field in product.keys() else ""
    existing_path = None
    if str(image_path or "").startswith(PRODUCT_IMAGE_DATA_PREFIX):
        existing_path = resolve_product_image_path(str(image_path)[len(PRODUCT_IMAGE_DATA_PREFIX) :])
    elif not image_path:
        existing_path = _fallback_product_image_path(product["bld_no"], slot)

    archived_images: list[tuple[Path, Path]] = []
    thumbnail_backups: list[tuple[Path, Path]] = []
    try:
        image_targets = {path for path in (existing_path, destination if destination.exists() else None) if path}
        for source in image_targets:
            archive_path = _archive_path(product["bld_no"], source)
            source.replace(archive_path)
            archived_images.append((source, archive_path))

        thumbnail_targets = {
            path
            for path in (
                product_image_thumb_path(existing_path.name) if existing_path else None,
                destination_thumb if destination_thumb.exists() else None,
            )
            if path and path.exists()
        }
        for source in thumbnail_targets:
            backup = source.with_name(f".{source.name}.{uuid4().hex}.replaced")
            source.replace(backup)
            thumbnail_backups.append((source, backup))

        temporary.replace(destination)
        temporary_thumb.replace(destination_thumb)
        conn.execute(
            f"UPDATE products SET {field} = ?, updated_at = ? WHERE id = ?",
            (f"{PRODUCT_IMAGE_DATA_PREFIX}{destination.name}", now_text(), product["id"]),
        )
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        destination.unlink(missing_ok=True)
        destination_thumb.unlink(missing_ok=True)
        for source, backup in reversed(thumbnail_backups):
            if backup.exists():
                backup.replace(source)
        for source, archive_path in reversed(archived_images):
            if archive_path.exists():
                archive_path.replace(source)
        temporary.unlink(missing_ok=True)
        temporary_thumb.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        temporary_thumb.unlink(missing_ok=True)
    for _source, backup in thumbnail_backups:
        backup.unlink(missing_ok=True)
    return destination


def delete_product_image(conn: sqlite3.Connection, product: sqlite3.Row, slot: int = 1, *, commit: bool = True) -> Path | None:
    field = image_slot_field(slot)
    image_path = product[field] if field in product.keys() else ""
    existing_path = None
    if str(image_path or "").startswith(PRODUCT_IMAGE_DATA_PREFIX):
        existing_path = resolve_product_image_path(str(image_path)[len(PRODUCT_IMAGE_DATA_PREFIX) :])

    archived_path = None
    if existing_path and existing_path.exists():
        existing_thumb = product_image_thumb_path(existing_path.name)
        if existing_thumb:
            existing_thumb.unlink(missing_ok=True)
        PRODUCT_IMAGE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        archive_dir = PRODUCT_IMAGE_ARCHIVE_DIR / safe_filename_part(product["bld_no"], "product")
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived_path = _unique_path(
            archive_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{existing_path.name}"
        )
        existing_path.replace(archived_path)

    conn.execute(
        f"UPDATE products SET {field} = '', updated_at = ? WHERE id = ?",
        (now_text(), product["id"]),
    )
    if commit:
        conn.commit()
    return archived_path
