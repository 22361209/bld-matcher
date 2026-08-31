from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.drawings import safe_filename_part
from app.product_image_processing import (
    PRODUCT_IMAGE_LARGE_MAX_BYTES,
    PRODUCT_IMAGE_LARGE_MAX_SIZE,
    PRODUCT_IMAGE_OUTPUT_SUFFIX,
    PRODUCT_IMAGE_THUMB_MAX_BYTES,
    PRODUCT_IMAGE_THUMB_SIZE,
    atomic_write_bytes,
    process_product_image,
)
from app.product_media import IMAGE_SLOT_FIELDS, PRODUCT_IMAGE_DATA_PREFIX, product_image_storage_name


@dataclass(frozen=True, slots=True)
class ProductImageMigrationJob:
    product_id: int
    bld_no: str
    field: str
    slot: int
    source: Path
    target: Path
    thumbnail: Path
    expected_reference: str


def _local_source(image_dir: Path, bld_no: str, slot: int, reference: str) -> Path | None:
    if reference.startswith(PRODUCT_IMAGE_DATA_PREFIX):
        name = Path(reference[len(PRODUCT_IMAGE_DATA_PREFIX) :]).name
        candidate = image_dir / name
        return candidate if candidate.is_file() else None
    if reference:
        return None
    slot_suffix = "" if slot == 1 else f"-{slot}"
    safe_bld = safe_filename_part(bld_no, "product")
    for suffix in (PRODUCT_IMAGE_OUTPUT_SUFFIX, ".jpg", ".jpeg", ".png"):
        candidate = image_dir / f"{safe_bld}{slot_suffix}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _is_compliant_image(
    path: Path,
    *,
    max_bytes: int,
    bounds: tuple[int, int],
) -> bool:
    try:
        if not path.is_file() or path.suffix.lower() != PRODUCT_IMAGE_OUTPUT_SUFFIX or path.stat().st_size > max_bytes:
            return False
        with Image.open(path) as opened:
            if opened.format != "WEBP" or opened.width > bounds[0] or opened.height > bounds[1]:
                return False
            opened.verify()
        return True
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return False


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _increment(report: dict[str, object], key: str, amount: int = 1) -> None:
    current = report.get(key, 0)
    report[key] = (current if isinstance(current, int) else 0) + amount


def _jobs(connection: sqlite3.Connection, image_dir: Path, thumb_dir: Path) -> tuple[list[ProductImageMigrationJob], list[str], int]:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(products)")}
    fields = [field for field in IMAGE_SLOT_FIELDS if field in columns]
    rows = connection.execute(
        f"SELECT id, bld_no, {', '.join(fields)} FROM products ORDER BY id"
    ).fetchall()
    jobs: list[ProductImageMigrationJob] = []
    missing: list[str] = []
    external = 0
    for row in rows:
        for slot, field in enumerate(fields, start=1):
            reference = str(row[field] or "").strip()
            source = _local_source(image_dir, str(row["bld_no"]), slot, reference)
            if source is None:
                if reference.startswith(PRODUCT_IMAGE_DATA_PREFIX):
                    missing.append(f"{row['bld_no']} 图片 {slot}: {Path(reference).name}")
                elif reference:
                    external += 1
                continue
            target = image_dir / product_image_storage_name(row["bld_no"], PRODUCT_IMAGE_OUTPUT_SUFFIX, slot)
            jobs.append(
                ProductImageMigrationJob(
                    product_id=int(row["id"]),
                    bld_no=str(row["bld_no"]),
                    field=field,
                    slot=slot,
                    source=source,
                    target=target,
                    thumbnail=thumb_dir / target.name,
                    expected_reference=f"{PRODUCT_IMAGE_DATA_PREFIX}{target.name}",
                )
            )
    return jobs, missing, external


def migrate_product_images(
    database_path: Path,
    image_dir: Path,
    thumb_dir: Path,
    *,
    apply: bool,
    report_path: Path | None = None,
) -> dict[str, object]:
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report: dict[str, object] = {
        "started_at": started_at,
        "mode": "apply" if apply else "check",
        "database": str(database_path),
        "images": 0,
        "compliant": 0,
        "needs_conversion": 0,
        "converted": 0,
        "relinked": 0,
        "missing": 0,
        "external": 0,
        "failed": 0,
        "removed_source_files": 0,
        "removed_source_bytes": 0,
        "errors": [],
    }
    if not database_path.is_file():
        report["status"] = "database_missing"
        if report_path:
            atomic_write_bytes(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        return report

    image_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    removal_candidates: set[Path] = set()
    protected_thumbnails: set[Path] = set()
    successful_sources: Counter[Path] = Counter()
    try:
        if not _table_exists(connection, "products"):
            report["status"] = "products_table_missing"
            return report
        jobs, missing, external = _jobs(connection, image_dir, thumb_dir)
        source_usage = Counter(job.source.resolve() for job in jobs)
        report["images"] = len(jobs)
        report["missing"] = len(missing)
        report["external"] = external
        errors = list(missing)

        for job in jobs:
            large_ok = _is_compliant_image(
                job.source,
                max_bytes=PRODUCT_IMAGE_LARGE_MAX_BYTES,
                bounds=PRODUCT_IMAGE_LARGE_MAX_SIZE,
            ) and job.source.resolve() == job.target.resolve()
            thumb_ok = _is_compliant_image(
                job.thumbnail,
                max_bytes=PRODUCT_IMAGE_THUMB_MAX_BYTES,
                bounds=PRODUCT_IMAGE_THUMB_SIZE,
            )
            row = connection.execute(
                f"SELECT {job.field} FROM products WHERE id = ?",
                (job.product_id,),
            ).fetchone()
            reference_ok = row is not None and str(row[job.field] or "") == job.expected_reference
            if large_ok and thumb_ok and reference_ok:
                _increment(report, "compliant")
                continue

            _increment(report, "needs_conversion")
            if not apply:
                continue
            try:
                if large_ok and thumb_ok:
                    connection.execute(
                        f"UPDATE products SET {job.field} = ? WHERE id = ?",
                        (job.expected_reference, job.product_id),
                    )
                    connection.commit()
                    _increment(report, "relinked")
                    continue

                processed = process_product_image(job.source)
                atomic_write_bytes(job.target, processed.large)
                atomic_write_bytes(job.thumbnail, processed.thumbnail)
                protected_thumbnails.add(job.thumbnail.resolve())
                connection.execute(
                    f"UPDATE products SET {job.field} = ? WHERE id = ?",
                    (job.expected_reference, job.product_id),
                )
                connection.commit()
                _increment(report, "converted")
                resolved_source = job.source.resolve()
                successful_sources[resolved_source] += 1
                if resolved_source != job.target.resolve():
                    removal_candidates.add(job.source)
            except (OSError, sqlite3.Error, ValueError) as exc:
                connection.rollback()
                _increment(report, "failed")
                errors.append(f"{job.bld_no} 图片 {job.slot}: {exc}")

        if apply:
            for source in sorted(removal_candidates):
                resolved_source = source.resolve()
                if successful_sources[resolved_source] != source_usage[resolved_source] or not source.exists():
                    continue
                try:
                    source_bytes = source.stat().st_size
                    source.unlink()
                    for old_thumb in {
                        thumb_dir / source.name,
                        thumb_dir / f"{source.stem}{PRODUCT_IMAGE_OUTPUT_SUFFIX}",
                    }:
                        if old_thumb.exists() and old_thumb.resolve() not in protected_thumbnails:
                            old_thumb.unlink()
                    _increment(report, "removed_source_files")
                    _increment(report, "removed_source_bytes", source_bytes)
                except OSError as exc:
                    _increment(report, "failed")
                    errors.append(f"清理旧图片 {source.name}: {exc}")

        report["errors"] = errors[:100]
        report["status"] = "ok" if not errors and not report["failed"] else "completed_with_errors"
        return report
    finally:
        connection.close()
        report["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if report_path:
            atomic_write_bytes(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
