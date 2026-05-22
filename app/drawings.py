from __future__ import annotations

import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from werkzeug.datastructures import FileStorage

from .config import DATA_DIR, DRAWING_ARCHIVE_DIR, DRAWING_PDF_DIR
from .database import now_text
from .matcher import split_codes


INVALID_FILENAME_CHARS = r'[\\/:*?"<>|\r\n]+'


def safe_filename_part(value: object, fallback: str = "file") -> str:
    text = "" if value is None else str(value)
    text = re.sub(INVALID_FILENAME_CHARS, "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text[:120].strip(" ._") or fallback)


def drawing_storage_name(bld_no: object) -> str:
    return f"{safe_filename_part(bld_no, 'drawing')}.pdf"


def _rewind_file(file: FileStorage) -> None:
    try:
        file.stream.seek(0)
    except (AttributeError, OSError):
        pass


def validate_product_drawing_file(file: FileStorage) -> None:
    original_name = Path(file.filename or "").name.strip()
    if not original_name:
        raise ValueError("请选择 PDF 图纸文件。")
    if Path(original_name).suffix.lower() != ".pdf":
        raise ValueError("图纸文件目前只支持 PDF。")
    try:
        header = file.stream.read(5)
    finally:
        _rewind_file(file)
    if not header:
        raise ValueError("PDF 图纸文件为空。")
    if header != b"%PDF-":
        raise ValueError("文件内容不是有效的 PDF。")


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


def _relative_to_data(path: Path) -> str:
    return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()


def resolve_drawing_path(relative_path: object) -> Path | None:
    text = str(relative_path or "").strip()
    if not text:
        return None
    path = (DATA_DIR / text).resolve()
    data_root = DATA_DIR.resolve()
    if data_root != path and data_root not in path.parents:
        return None
    return path if path.exists() and path.is_file() else None


def product_drawing_path(product: sqlite3.Row | dict) -> Path | None:
    keys = product.keys()
    relative = product["drawing_path"] if "drawing_path" in keys else ""
    path = resolve_drawing_path(relative)
    if path:
        return path

    bld_no = product["bld_no"] if "bld_no" in keys else ""
    fallback = DRAWING_PDF_DIR / drawing_storage_name(bld_no)
    return fallback if fallback.exists() and fallback.is_file() else None


def save_product_drawing(conn: sqlite3.Connection, product: sqlite3.Row, file: FileStorage, *, commit: bool = True) -> Path:
    original_name = Path(file.filename or "").name.strip()
    if not original_name:
        raise ValueError("请选择 PDF 图纸文件。")
    if Path(original_name).suffix.lower() != ".pdf":
        raise ValueError("图纸文件目前只支持 PDF。")

    DRAWING_PDF_DIR.mkdir(parents=True, exist_ok=True)
    DRAWING_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    destination = DRAWING_PDF_DIR / drawing_storage_name(product["bld_no"])
    temporary = destination.with_name(f".{destination.stem}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.uploading.pdf")
    file.save(temporary)
    try:
        if temporary.stat().st_size == 0:
            raise ValueError("PDF 图纸文件为空。")
        with temporary.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError("文件内容不是有效的 PDF。")

        if destination.exists():
            archive_dir = DRAWING_ARCHIVE_DIR / safe_filename_part(product["bld_no"], "drawing")
            archive_dir.mkdir(parents=True, exist_ok=True)
            previous_name = product["drawing_original_name"] if "drawing_original_name" in product.keys() else destination.name
            archive_name = safe_filename_part(Path(previous_name or destination.name).stem, destination.stem)
            archive_path = _unique_path(archive_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{archive_name}.pdf")
            destination.replace(archive_path)

        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    conn.execute(
        """
        UPDATE products
        SET drawing_path = ?, drawing_original_name = ?, drawing_updated_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (_relative_to_data(destination), original_name, now_text(), now_text(), product["id"]),
    )
    if commit:
        conn.commit()
    return destination


def split_bld_numbers(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\s+/\s+|[\n,，;；]+", text)
    seen: list[str] = []
    for part in parts:
        bld_no = part.strip()
        if bld_no and bld_no not in seen:
            seen.append(bld_no)
    return seen


def drawing_suffix_for_row(row: dict) -> str:
    matched_codes = row.get("matched_oe_codes") or []
    if matched_codes:
        return safe_filename_part("_".join(str(code) for code in matched_codes[:3]), "code")
    codes = split_codes(row.get("oe"))
    if codes:
        return safe_filename_part("_".join(codes[:3]), "code")
    return safe_filename_part(row.get("oe") or row.get("row") or "code", "code")


def _drawing_entry_name(original_name: object, path: Path, suffix: str, used_names: set[str]) -> str:
    stem = safe_filename_part(Path(str(original_name or "")).stem or path.stem, path.stem)
    if suffix:
        stem = safe_filename_part(f"{stem}_{suffix}", stem)
    candidate = f"{stem}.pdf"
    counter = 2
    while candidate in used_names:
        candidate = f"{stem}_{counter}.pdf"
        counter += 1
    used_names.add(candidate)
    return candidate


def build_drawings_zip(conn: sqlite3.Connection, summary_rows: list[dict], zip_path: Path) -> dict:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    missing: list[str] = []
    added = 0

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in summary_rows:
            bld_numbers = split_bld_numbers(row.get("bld_no"))
            if not bld_numbers:
                continue
            suffix = drawing_suffix_for_row(row)
            for bld_no in bld_numbers:
                product = conn.execute("SELECT * FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
                path = product_drawing_path(product) if product else None
                if not path:
                    missing.append(f"第 {row.get('row', '')} 行：{bld_no} 未找到 PDF 图纸")
                    continue
                original_name = product["drawing_original_name"] if product and "drawing_original_name" in product.keys() else path.name
                archive.write(path, _drawing_entry_name(original_name, path, suffix, used_names))
                added += 1

        if missing:
            archive.writestr("缺少图纸.txt", "\n".join(missing) + "\n")
        if added == 0 and not missing:
            archive.writestr("图纸打包说明.txt", "匹配结果中没有可打包的 BLD NO.\n")

    return {"path": zip_path, "added": added, "missing": len(missing)}
