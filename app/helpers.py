from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import url_for
from werkzeug.utils import secure_filename

from .config import BASE_DIR, CATALOG_PATH, DATA_DIR, DB_PATH, MANUAL_MAP_PATH, OUTPUT_DIR
from .database import bootstrap_from_excel, connect, rows_for_catalog
from .matcher import ProductCatalog, load_manual_map


def product_image_url(product) -> str:
    explicit = (product["image_path"] if "image_path" in product.keys() else "") or ""
    if explicit:
        if explicit.startswith(("http://", "https://", "/static/")):
            return explicit
        return url_for("static", filename=explicit.lstrip("/"))

    bld_no = product["bld_no"] if "bld_no" in product.keys() else ""
    for suffix in ("jpg", "jpeg", "png", "webp"):
        relative = f"product_images/{bld_no}.{suffix}"
        if (BASE_DIR / "static" / relative).exists():
            return url_for("static", filename=relative)
    return ""


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


def clean_original_filename(filename: str, fallback_suffix: str = "") -> str:
    name = Path(filename or "").name.replace("/", "").replace("\\", "").strip()
    if not name:
        name = f"source{fallback_suffix}"
    if fallback_suffix and not Path(name).suffix:
        name = f"{name}{fallback_suffix}"
    return name


def result_output_path(original_filename: str, fallback_suffix: str = "") -> Path:
    source_name = clean_original_filename(original_filename, fallback_suffix=fallback_suffix)
    prefix = f"re{datetime.now().strftime('%y%m%d')}"
    candidate = OUTPUT_DIR / f"{prefix}{source_name}"
    if not candidate.exists():
        return candidate

    stem = Path(source_name).stem
    suffix = Path(source_name).suffix
    counter = 2
    while True:
        numbered = OUTPUT_DIR / f"{prefix}{stem}_{counter}{suffix}"
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
