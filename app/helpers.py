from __future__ import annotations

import shutil
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from flask import g, url_for
from werkzeug.utils import secure_filename

from .config import (
    BASE_DIR,
    CATALOG_PATH,
    DATA_DIR,
    DB_PATH,
    MANUAL_MAP_PATH,
    OUTPUT_DIR,
    PRODUCT_IMAGE_DATA_PREFIX,
    UPLOAD_DIR,
)
from .database import bootstrap_from_excel, connect, rows_for_catalog
from .matcher import ProductCatalog, load_manual_map


PRODUCT_IMAGE_SLOT_FIELDS = ("image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5")


@lru_cache(maxsize=2048)
def _default_product_image_relative(bld_no: str) -> str:
    for suffix in ("jpg", "jpeg", "png", "webp"):
        relative = f"product_images/{bld_no}.{suffix}"
        if (BASE_DIR / "static" / relative).exists():
            return relative
    return ""


@lru_cache(maxsize=2048)
def _default_product_thumb_relative(bld_no: str) -> str:
    for suffix in ("jpg", "jpeg", "png", "webp"):
        relative = f"product_images/thumbs/{bld_no}.{suffix}"
        if (BASE_DIR / "static" / relative).exists():
            return relative
    return ""


def _product_keys(product) -> set[str]:
    return set(product.keys())


def product_image_url(product, slot: int = 1) -> str:
    fields = PRODUCT_IMAGE_SLOT_FIELDS
    field = fields[slot - 1] if 1 <= slot <= len(fields) else fields[0]
    keys = _product_keys(product)
    explicit = (product[field] if field in keys else "") or ""
    if explicit:
        if explicit.startswith(PRODUCT_IMAGE_DATA_PREFIX):
            return url_for("product_image_data", name=explicit[len(PRODUCT_IMAGE_DATA_PREFIX) :])
        if explicit.startswith(("http://", "https://", "/static/")):
            return explicit
        return url_for("static", filename=explicit.lstrip("/"))

    if slot != 1:
        return ""
    bld_no = product["bld_no"] if "bld_no" in product.keys() else ""
    relative = _default_product_image_relative(bld_no)
    return url_for("static", filename=relative) if relative else ""


def product_image_thumb_url(product, slot: int = 1) -> str:
    fields = PRODUCT_IMAGE_SLOT_FIELDS
    field = fields[slot - 1] if 1 <= slot <= len(fields) else fields[0]
    keys = _product_keys(product)
    explicit = (product[field] if field in keys else "") or ""
    if explicit:
        if explicit.startswith(PRODUCT_IMAGE_DATA_PREFIX):
            return url_for("product_image_thumb_data", name=explicit[len(PRODUCT_IMAGE_DATA_PREFIX) :])
        return product_image_url(product, slot)

    if slot != 1:
        return ""
    bld_no = product["bld_no"] if "bld_no" in product.keys() else ""
    relative = _default_product_thumb_relative(bld_no) or _default_product_image_relative(bld_no)
    return url_for("static", filename=relative) if relative else ""


def product_image_urls(product) -> list[dict[str, str]]:
    images = []
    for slot in range(1, len(PRODUCT_IMAGE_SLOT_FIELDS) + 1):
        url = product_image_url(product, slot)
        if not url:
            continue
        images.append(
            {
                "slot": str(slot),
                "label": f"图片 {slot}",
                "url": url,
                "thumb": product_image_thumb_url(product, slot) or url,
            }
        )
    return images


def bootstrap_catalog() -> None:
    if CATALOG_PATH.exists():
        return
    candidates = sorted((BASE_DIR / "产品目录").glob("*.xlsx"))
    if candidates:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidates[0], CATALOG_PATH)


def load_catalog() -> ProductCatalog | None:
    bootstrap_catalog()
    bootstrap_from_excel(DB_PATH, CATALOG_PATH)
    with connect(DB_PATH) as conn:
        products, aliases = rows_for_catalog(conn)
    if not products:
        return None
    legacy_map = load_manual_map(MANUAL_MAP_PATH)
    aliases.update(legacy_map)
    return ProductCatalog(products, manual_map=aliases)


def safe_upload_name(filename: str) -> str:
    name = secure_filename(filename)
    suffix = Path(filename).suffix.lower()
    if not name:
        return f"upload-{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}"
    if suffix and not Path(name).suffix:
        return f"{name}{suffix}"
    return name


def user_file_label() -> str:
    user = getattr(g, "user", None)
    if not user:
        return "anonymous"
    username = str(user["username"] if "username" in user.keys() else "").strip()
    label = secure_filename(username)
    if label:
        return label
    user_id = str(user["id"] if "id" in user.keys() else "").strip()
    return f"user{user_id}" if user_id else "user"


def user_dir_slug() -> str:
    user = getattr(g, "user", None)
    if not user:
        return "anonymous"
    user_id = str(user["id"] if "id" in user.keys() else "").strip() or "0"
    return f"u{user_id}-{user_file_label()}"


def user_upload_dir(*, create: bool = True) -> Path:
    path = UPLOAD_DIR / user_dir_slug()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_output_dir(*, create: bool = True) -> Path:
    path = OUTPUT_DIR / user_dir_slug()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_upload_path(filename: str, prefix: str = "") -> Path:
    safe_name = safe_upload_name(filename)
    label = user_file_label()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix_text = f"{prefix}-" if prefix else ""
    return user_upload_dir() / f"{prefix_text}{timestamp}-{label}-{safe_name}"


def clean_original_filename(filename: str, fallback_suffix: str = "") -> str:
    name = Path(filename or "").name.replace("/", "").replace("\\", "").strip()
    if not name:
        name = f"source{fallback_suffix}"
    if fallback_suffix and not Path(name).suffix:
        name = f"{name}{fallback_suffix}"
    return name


def result_output_path(original_filename: str, fallback_suffix: str = "", output_dir: Path | None = None) -> Path:
    source_name = clean_original_filename(original_filename, fallback_suffix=fallback_suffix)
    destination = output_dir or user_output_dir()
    destination.mkdir(parents=True, exist_ok=True)
    prefix = f"re{datetime.now().strftime('%y%m%d')}-{user_file_label()}-"
    candidate = destination / f"{prefix}{source_name}"
    if not candidate.exists():
        return candidate

    stem = Path(source_name).stem
    suffix = Path(source_name).suffix
    counter = 2
    while True:
        numbered = destination / f"{prefix}{stem}_{counter}{suffix}"
        if not numbered.exists():
            return numbered
        counter += 1


def user_recent_outputs(pattern: str = "*", limit: int = 8) -> list[Path]:
    directory = user_output_dir(create=False)
    if not directory.exists():
        return []
    return sorted((path for path in directory.rglob(pattern) if path.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def all_recent_outputs(pattern: str = "*", limit: int = 8) -> list[Path]:
    if not OUTPUT_DIR.exists():
        return []
    files = [path for path in OUTPUT_DIR.rglob(pattern) if path.is_file()]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def download_name(path: Path) -> str:
    resolved = path.resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root in resolved.parents:
        return resolved.relative_to(output_root).as_posix()
    return path.name


def unique_prefixed_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        numbered = directory / f"{stem}_{counter}{suffix}"
        if not numbered.exists():
            return numbered
        counter += 1


def column_display(index: int) -> str:
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label
