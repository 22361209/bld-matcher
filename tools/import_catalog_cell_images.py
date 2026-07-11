#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
import shutil
import sqlite3
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.product_media import generate_product_image_thumb  # noqa: E402

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "products.sqlite3"
PRODUCT_IMAGE_DIR = DATA_DIR / "product_images"
PRODUCT_IMAGE_PREFIX = "data_product_images/"

NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_ETC = "{http://www.wps.cn/officeDocument/2017/etCustomData}"


@dataclass(frozen=True)
class ImageImportRow:
    row_number: int
    bld_no: str
    image_id: str
    media_path: str
    suffix: str
    flip_h: bool = False
    flip_v: bool = False


def compact_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def safe_filename_part(value: object, fallback: str = "product") -> str:
    text = compact_text(value)
    text = re.sub(r"[\\\\/:*?\"<>|]+", "-", text)
    text = re.sub(r"\\s+", "-", text).strip(".-")
    return text or fallback


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def col_letters(cell_ref: str) -> str:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    return match.group(1) if match else ""


def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall(f"{NS_MAIN}si"):
        parts = [node.text or "" for node in si.iter(f"{NS_MAIN}t")]
        strings.append("".join(parts))
    return strings


def cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    formula = cell.find(f"{NS_MAIN}f")
    if formula is not None and formula.text:
        return html.unescape(formula.text)

    value = cell.find(f"{NS_MAIN}v")
    if value is None or value.text is None:
        inline = cell.find(f"{NS_MAIN}is/{NS_MAIN}t")
        return inline.text if inline is not None and inline.text else ""

    text = value.text
    if cell.attrib.get("t") == "s":
        try:
            return shared_strings[int(text)]
        except (IndexError, ValueError):
            return ""
    return text


def relationship_targets(zf: zipfile.ZipFile) -> dict[str, str]:
    root = ET.fromstring(zf.read("xl/_rels/cellimages.xml.rels"))
    targets: dict[str, str] = {}
    for rel in root.findall(f"{NS_REL}Relationship"):
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rel_id and target:
            targets[rel_id] = "xl/" + target.lstrip("/")
    return targets


def cell_image_targets(zf: zipfile.ZipFile) -> dict[str, tuple[str, bool, bool]]:
    rel_targets = relationship_targets(zf)
    root = ET.fromstring(zf.read("xl/cellimages.xml"))
    image_targets: dict[str, tuple[str, bool, bool]] = {}
    for cell_image in root.findall(f"{NS_ETC}cellImage"):
        c_nv_pr = cell_image.find(f".//{NS_XDR}cNvPr")
        blip = cell_image.find(f".//{NS_A}blip")
        if c_nv_pr is None or blip is None:
            continue
        image_id = c_nv_pr.attrib.get("name", "")
        if not image_id.startswith("ID_"):
            continue
        rel_id = blip.attrib.get(f"{NS_R}embed", "")
        target = rel_targets.get(rel_id, "")
        if target:
            xfrm = cell_image.find(f".//{NS_A}xfrm")
            flip_h = xfrm is not None and xfrm.attrib.get("flipH") in {"1", "true", "True"}
            flip_v = xfrm is not None and xfrm.attrib.get("flipV") in {"1", "true", "True"}
            image_targets[image_id.upper()] = (target, flip_h, flip_v)
    return image_targets


def workbook_rows(zf: zipfile.ZipFile) -> tuple[list[dict[str, str]], dict[str, str]]:
    shared_strings = load_shared_strings(zf)
    sheet_root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    rows: list[dict[str, str]] = []
    header_by_col: dict[str, str] = {}

    for row in sheet_root.findall(f".//{NS_MAIN}row"):
        row_number = int(row.attrib.get("r", "0") or 0)
        values: dict[str, str] = {"__row__": str(row_number)}
        for cell in row.findall(f"{NS_MAIN}c"):
            col = col_letters(cell.attrib.get("r", ""))
            if not col:
                continue
            values[col] = compact_text(cell_value(cell, shared_strings))
        rows.append(values)

        headers = {col: value for col, value in values.items() if col != "__row__" and value}
        if "BLD NO." in headers.values() and "Pic" in headers.values():
            header_by_col = headers

    if not header_by_col:
        raise RuntimeError("未找到包含 BLD NO. 和 Pic 的表头行。")
    return rows, header_by_col


def discover_import_rows(workbook_path: Path) -> tuple[list[ImageImportRow], dict[str, int]]:
    with zipfile.ZipFile(workbook_path) as zf:
        if "xl/cellimages.xml" not in zf.namelist():
            raise RuntimeError("工作簿中没有 xl/cellimages.xml，未检测到单元格图片。")
        image_targets = cell_image_targets(zf)
        rows, header_by_col = workbook_rows(zf)

    bld_col = next(col for col, value in header_by_col.items() if value == "BLD NO.")
    pic_col = next(col for col, value in header_by_col.items() if value == "Pic")
    header_row = max(int(row["__row__"]) for row in rows if row.get(bld_col) == "BLD NO.")

    imports: list[ImageImportRow] = []
    stats = {
        "excel_rows": 0,
        "pic_formulas": 0,
        "missing_media_mapping": 0,
        "unsupported_suffix": 0,
        "blank_bld": 0,
    }
    for row in rows:
        row_number = int(row["__row__"])
        if row_number <= header_row:
            continue
        bld_no = compact_text(row.get(bld_col, "")).upper()
        formula = row.get(pic_col, "")
        if not bld_no:
            stats["blank_bld"] += 1
            continue
        stats["excel_rows"] += 1
        match = re.search(r"ID_[A-F0-9]+", formula, flags=re.IGNORECASE)
        if not match:
            continue
        stats["pic_formulas"] += 1
        image_id = match.group(0).upper()
        image_target = image_targets.get(image_id)
        if not image_target:
            stats["missing_media_mapping"] += 1
            continue
        media_path, flip_h, flip_v = image_target
        suffix = Path(media_path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            stats["unsupported_suffix"] += 1
            continue
        imports.append(ImageImportRow(row_number, bld_no, image_id, media_path, suffix, flip_h, flip_v))
    return imports, stats


def backup_database(db_path: Path) -> Path:
    backup_path = db_path.with_name(f"products-backup-{timestamp()}-before-catalog-images.sqlite3")
    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup_path


def backup_image_dir(image_dir: Path) -> Path | None:
    if not image_dir.exists() or not any(image_dir.iterdir()):
        return None
    backup_path = image_dir.with_name(f"product_images-backup-{timestamp()}-before-catalog-images")
    shutil.copytree(image_dir, backup_path)
    return backup_path


def read_products(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return {
        compact_text(row["bld_no"]).upper(): row
        for row in conn.execute("SELECT id, bld_no, image_path FROM products")
    }


def transformed_image_bytes(raw: bytes, suffix: str, flip_h: bool, flip_v: bool) -> bytes:
    if not flip_h and not flip_v:
        return raw

    with Image.open(BytesIO(raw)) as image:
        transformed = image.copy()
    if flip_h:
        transformed = transformed.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if flip_v:
        transformed = transformed.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    output = BytesIO()
    if suffix in {".jpg", ".jpeg"}:
        if transformed.mode in {"RGBA", "LA", "P"}:
            transformed = transformed.convert("RGB")
        transformed.save(output, format="JPEG", quality=95)
    elif suffix == ".webp":
        transformed.save(output, format="WEBP")
    else:
        transformed.save(output, format="PNG")
    return output.getvalue()


def import_images(workbook_path: Path, db_path: Path, dry_run: bool) -> dict[str, object]:
    imports, stats = discover_import_rows(workbook_path)

    with sqlite3.connect(db_path) as conn:
        products = read_products(conn)

    seen_bld: set[str] = set()
    duplicate_bld: set[str] = set()
    duplicate_rows = 0
    matched: list[ImageImportRow] = []
    missing_products: list[str] = []
    for item in imports:
        if item.bld_no in seen_bld:
            duplicate_bld.add(item.bld_no)
            duplicate_rows += 1
            continue
        seen_bld.add(item.bld_no)
        if item.bld_no not in products:
            missing_products.append(item.bld_no)
            continue
        matched.append(item)

    result: dict[str, object] = {
        **stats,
        "image_rows": len(imports),
        "matched_products": len(matched),
        "missing_products": len(missing_products),
        "duplicate_bld": len(duplicate_bld),
        "duplicate_rows_skipped": duplicate_rows,
        "flip_h_images": sum(1 for item in matched if item.flip_h),
        "flip_v_images": sum(1 for item in matched if item.flip_v),
        "dry_run": dry_run,
        "db_backup": "",
        "image_backup": "",
        "written": 0,
    }
    if dry_run:
        result["missing_product_samples"] = missing_products[:20]
        result["duplicate_bld_samples"] = sorted(duplicate_bld)[:20]
        return result

    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    result["db_backup"] = str(backup_database(db_path))
    image_backup = backup_image_dir(PRODUCT_IMAGE_DIR)
    result["image_backup"] = str(image_backup or "")

    with zipfile.ZipFile(workbook_path) as zf, sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        product_rows = read_products(conn)
        current_time = now_text()
        written = 0
        for item in matched:
            product = product_rows[item.bld_no]
            filename = f"{safe_filename_part(product['bld_no'])}{item.suffix}"
            destination = PRODUCT_IMAGE_DIR / filename
            raw = zf.read(item.media_path)
            with destination.open("wb") as handle:
                handle.write(transformed_image_bytes(raw, item.suffix, item.flip_h, item.flip_v))
            generate_product_image_thumb(destination)
            conn.execute(
                "UPDATE products SET image_path = ?, updated_at = ? WHERE id = ?",
                (f"{PRODUCT_IMAGE_PREFIX}{filename}", current_time, product["id"]),
            )
            written += 1
        conn.commit()
        result["written"] = written
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Excel cell images into product image slots.")
    parser.add_argument("workbook", type=Path, help="Product catalog .xlsx containing DISPIMG cell images.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path.")
    parser.add_argument("--apply", action="store_true", help="Write images and update products.sqlite3.")
    args = parser.parse_args()

    workbook = args.workbook.resolve()
    db_path = args.db.resolve()
    if not workbook.exists():
        print(f"Workbook not found: {workbook}", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    result = import_images(workbook, db_path, dry_run=not args.apply)
    for key, value in result.items():
        if isinstance(value, list):
            value = ", ".join(map(str, value)) if value else ""
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
