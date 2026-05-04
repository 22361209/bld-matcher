from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from .config import PRODUCT_IMAGE_ARCHIVE_DIR, PRODUCT_IMAGE_DATA_PREFIX, PRODUCT_IMAGE_DIR, PRODUCT_IMAGE_THUMB_DIR
from .database import now_text
from .drawings import safe_filename_part


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_SLOT_FIELDS = ("image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5")
PRODUCT_IMAGE_THUMB_SIZE = (160, 120)


def image_slot_field(slot: int) -> str:
    if not 1 <= slot <= len(IMAGE_SLOT_FIELDS):
        raise ValueError("产品图片位置必须在 1 到 5 之间。")
    return IMAGE_SLOT_FIELDS[slot - 1]


def product_image_storage_name(bld_no: object, suffix: str, slot: int = 1) -> str:
    suffix_text = "" if slot == 1 else f"-{slot}"
    return f"{safe_filename_part(bld_no, 'product')}{suffix_text}{suffix.lower()}"


def _is_supported_image(path: Path, suffix: str) -> bool:
    with path.open("rb") as handle:
        header = handle.read(16)
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


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
    path = (PRODUCT_IMAGE_THUMB_DIR / safe_name).resolve()
    root = PRODUCT_IMAGE_THUMB_DIR.resolve()
    if root != path.parent:
        return None
    return path


def generate_product_image_thumb(source: Path) -> Path | None:
    destination = product_image_thumb_path(source.name)
    if destination is None:
        return None
    PRODUCT_IMAGE_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    format_name = "JPEG" if suffix in {".jpg", ".jpeg"} else suffix.lstrip(".").upper()
    if format_name == "JPG":
        format_name = "JPEG"
    if format_name not in {"JPEG", "PNG", "WEBP"}:
        return None

    temporary = destination.with_name(f".{destination.stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.thumb{suffix}")
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.thumbnail(PRODUCT_IMAGE_THUMB_SIZE, Image.Resampling.LANCZOS)

            save_kwargs = {}
            if format_name in {"JPEG", "WEBP"} and image.mode in {"RGBA", "LA", "P"}:
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            elif format_name == "JPEG" and image.mode != "RGB":
                image = image.convert("RGB")

            if format_name == "JPEG":
                save_kwargs.update({"quality": 82, "optimize": True})
            elif format_name == "PNG":
                save_kwargs.update({"optimize": True})
            elif format_name == "WEBP":
                save_kwargs.update({"quality": 82, "method": 4})

            image.save(temporary, format=format_name, **save_kwargs)
        temporary.replace(destination)
        return destination
    except (OSError, UnidentifiedImageError, ValueError):
        temporary.unlink(missing_ok=True)
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
        return source
    try:
        thumb_is_current = destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        thumb_is_current = False
    if thumb_is_current:
        return destination
    return generate_product_image_thumb(source) or source


def save_product_image(conn: sqlite3.Connection, product: sqlite3.Row, file: FileStorage, slot: int = 1) -> Path:
    field = image_slot_field(slot)
    original_name = Path(file.filename or "").name.strip()
    if not original_name:
        raise ValueError("请选择产品图片文件。")
    suffix = Path(original_name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("产品图片支持 JPG、PNG、WEBP。")

    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_IMAGE_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    destination = PRODUCT_IMAGE_DIR / product_image_storage_name(product["bld_no"], suffix, slot)
    temporary = destination.with_name(f".{destination.stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.uploading{suffix}")
    file.save(temporary)
    try:
        if temporary.stat().st_size == 0:
            raise ValueError("产品图片文件为空。")
        if not _is_supported_image(temporary, suffix):
            raise ValueError("文件内容不是支持的图片格式。")

        existing_path = None
        image_path = product[field] if field in product.keys() else ""
        if str(image_path or "").startswith(PRODUCT_IMAGE_DATA_PREFIX):
            existing_path = resolve_product_image_path(str(image_path)[len(PRODUCT_IMAGE_DATA_PREFIX) :])
        if existing_path and existing_path.exists():
            existing_thumb = product_image_thumb_path(existing_path.name)
            if existing_thumb:
                existing_thumb.unlink(missing_ok=True)
            archive_dir = PRODUCT_IMAGE_ARCHIVE_DIR / safe_filename_part(product["bld_no"], "product")
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = _unique_path(
                archive_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{existing_path.name}"
            )
            existing_path.replace(archive_path)

        temporary.replace(destination)
        generate_product_image_thumb(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    conn.execute(
        f"UPDATE products SET {field} = ?, updated_at = ? WHERE id = ?",
        (f"{PRODUCT_IMAGE_DATA_PREFIX}{destination.name}", now_text(), product["id"]),
    )
    conn.commit()
    return destination
