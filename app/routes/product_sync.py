from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR, DB_PATH, DRAWING_DIR, PRODUCT_IMAGE_DIR
from app.database import connect, log_event
from app.helpers import user_file_label, user_output_dir, user_upload_dir, user_upload_path
from app.locks import ImportLockError, import_lock
from app.security import actor_name, permission_required


PACKAGE_SUFFIX = ".tar.gz"
PRODUCT_DB_NAMES = ("data/products.sqlite3", "products.sqlite3")
MANIFEST_NAME = "manifest.json"
PRODUCT_TABLE = "products"
MEDIA_DIRS = {
    "drawings": ("data/drawings", DRAWING_DIR),
    "product_images": ("data/product_images", PRODUCT_IMAGE_DIR),
}


@dataclass
class ProductDiff:
    new_count: int
    updated_count: int
    conflict_count: int
    unchanged_count: int
    local_only_count: int
    rows: list[dict[str, object]]


def _now_label() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _product_columns(conn: sqlite3.Connection, schema: str = "main") -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA {schema}.table_info({PRODUCT_TABLE})")]


def _create_products_only_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(DB_PATH) as source, sqlite3.connect(path) as target:
        target.row_factory = sqlite3.Row
        schema_row = source.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (PRODUCT_TABLE,),
        ).fetchone()
        if not schema_row or not schema_row["sql"]:
            raise RuntimeError("当前数据库缺少 products 表。")
        target.execute(schema_row["sql"])
        columns = _product_columns(source)
        column_sql = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        rows = source.execute(f"SELECT {column_sql} FROM products ORDER BY bld_no COLLATE BLD_NATURAL").fetchall()
        target.executemany(
            f"INSERT INTO products ({column_sql}) VALUES ({placeholders})",
            ([row[column] for column in columns] for row in rows),
        )
        target.commit()


def _add_directory_to_tar(archive: tarfile.TarFile, source: Path, arcname: str) -> int:
    if not source.exists():
        return 0
    count = 0
    for path in sorted(source.rglob("*")):
        if path.is_file():
            archive.add(path, arcname=str(Path(arcname) / path.relative_to(source)))
            count += 1
    return count


def _export_package(*, include_drawings: bool, include_images: bool) -> Path:
    output_path = user_output_dir() / f"product-data-{user_file_label()}-{_now_label()}{PACKAGE_SUFFIX}"
    manifest = {
        "package_type": "bld_product_data",
        "version": 1,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "includes": {
            "products": True,
            "drawings": include_drawings,
            "product_images": include_images,
        },
        "media_files": {"drawings": 0, "product_images": 0},
    }
    with tempfile.TemporaryDirectory(prefix="bld-product-export-") as temporary_dir:
        product_db = Path(temporary_dir) / "products.sqlite3"
        _create_products_only_db(product_db)
        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(product_db, arcname="data/products.sqlite3")
            manifest_path = Path(temporary_dir) / MANIFEST_NAME
            if include_drawings:
                manifest["media_files"]["drawings"] = _add_directory_to_tar(archive, DRAWING_DIR, "data/drawings")
            if include_images:
                manifest["media_files"]["product_images"] = _add_directory_to_tar(archive, PRODUCT_IMAGE_DIR, "data/product_images")
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            archive.add(manifest_path, arcname=MANIFEST_NAME)
    return output_path


def _safe_extract_package(package_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(package_path, "r:gz") as archive:
        destination_resolved = destination.resolve()
        for member in archive.getmembers():
            member_path = (destination / member.name).resolve()
            if destination_resolved != member_path and destination_resolved not in member_path.parents:
                raise ValueError(f"数据包包含不安全路径：{member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"数据包不能包含链接文件：{member.name}")
        archive.extractall(destination)


def _find_product_db(extracted_dir: Path) -> Path:
    for name in PRODUCT_DB_NAMES:
        path = extracted_dir / name
        if path.is_file():
            return path
    raise ValueError("数据包里没有 products.sqlite3。")


def _read_manifest(extracted_dir: Path) -> dict[str, object]:
    path = extracted_dir / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _validate_product_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise ValueError(f"products.sqlite3 完整性检查失败：{ok}")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (PRODUCT_TABLE,),
        ).fetchone()
        if not exists:
            raise ValueError("products.sqlite3 缺少 products 表。")


def _row_changed(local_row: sqlite3.Row | None, incoming_row: sqlite3.Row, columns: list[str]) -> bool:
    if local_row is None:
        return True
    return any(local_row[column] != incoming_row[column] for column in columns if column != "id")


def _parse_updated_at(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _incoming_is_older(local_row: sqlite3.Row | None, incoming_row: sqlite3.Row) -> bool:
    if local_row is None:
        return False
    local_updated = _parse_updated_at(local_row["updated_at"])
    incoming_updated = _parse_updated_at(incoming_row["updated_at"])
    if not local_updated or not incoming_updated:
        return False
    return incoming_updated < local_updated


def _diff_products(package_db: Path, *, limit: int = 50) -> ProductDiff:
    with connect(DB_PATH) as conn:
        conn.execute(f"ATTACH {str(package_db)!r} AS incoming")
        local_columns = _product_columns(conn, "main")
        incoming_columns = _product_columns(conn, "incoming")
        if set(local_columns) != set(incoming_columns):
            raise ValueError("数据包 products 表结构与当前系统不一致，请先升级程序后再导入。")

        column_sql = ", ".join(local_columns)
        incoming_rows = conn.execute(f"SELECT {column_sql} FROM incoming.products ORDER BY bld_no COLLATE BLD_NATURAL").fetchall()
        local_by_bld = {
            row["bld_no"]: row
            for row in conn.execute("SELECT * FROM main.products")
        }
        rows: list[dict[str, object]] = []
        new_count = updated_count = conflict_count = unchanged_count = 0
        for row in incoming_rows:
            local_row = local_by_bld.get(row["bld_no"])
            if local_row is None:
                new_count += 1
                status = "new"
            elif _row_changed(local_row, row, local_columns) and _incoming_is_older(local_row, row):
                conflict_count += 1
                status = "conflict"
            elif _row_changed(local_row, row, local_columns):
                updated_count += 1
                status = "updated"
            else:
                unchanged_count += 1
                status = "unchanged"
            if status != "unchanged" and len(rows) < limit:
                rows.append(
                    {
                        "status": status,
                        "bld_no": row["bld_no"],
                        "local_updated_at": local_row["updated_at"] if local_row else "",
                        "incoming_updated_at": row["updated_at"],
                        "local_price": local_row["price_cny"] if local_row else None,
                        "incoming_price": row["price_cny"],
                    }
                )
        local_only_rows = conn.execute(
            """
            SELECT *
            FROM main.products
            WHERE bld_no NOT IN (SELECT bld_no FROM incoming.products)
            ORDER BY bld_no COLLATE BLD_NATURAL
            """
        ).fetchall()
        for local_row in local_only_rows:
            if len(rows) >= limit:
                break
            rows.append(
                {
                    "status": "local_only",
                    "bld_no": local_row["bld_no"],
                    "local_updated_at": local_row["updated_at"],
                    "incoming_updated_at": "",
                    "local_price": local_row["price_cny"],
                    "incoming_price": None,
                }
            )
        return ProductDiff(
            new_count=new_count,
            updated_count=updated_count,
            conflict_count=conflict_count,
            unchanged_count=unchanged_count,
            local_only_count=len(local_only_rows),
            rows=rows,
        )


def _media_file_count(extracted_dir: Path, key: str) -> int:
    relative, _destination = MEDIA_DIRS[key]
    source = extracted_dir / relative
    if not source.exists():
        return 0
    return sum(1 for path in source.rglob("*") if path.is_file())


def _package_upload_path() -> Path | None:
    raw_path = request.form.get("package_path", "")
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    upload_root = user_upload_dir(create=False).resolve()
    if upload_root != path and upload_root not in path.parents:
        return None
    if not path.is_file() or not path.name.endswith(PACKAGE_SUFFIX):
        return None
    return path


def _apply_products(package_db: Path, *, deactivate_local_only: bool = False) -> tuple[int, int, int, int, int]:
    with connect(DB_PATH) as conn:
        conn.execute(f"ATTACH {str(package_db)!r} AS incoming")
        columns = _product_columns(conn, "main")
        incoming_columns = _product_columns(conn, "incoming")
        if set(columns) != set(incoming_columns):
            raise ValueError("数据包 products 表结构与当前系统不一致，请先升级程序后再导入。")
        insert_columns = [column for column in columns if column != "id"]
        column_sql = ", ".join(insert_columns)
        placeholders = ", ".join("?" for _ in insert_columns)
        assignments = ", ".join(f"{column} = excluded.{column}" for column in insert_columns if column != "bld_no")
        new_count = updated_count = conflict_count = unchanged_count = 0
        deactivated_count = 0
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM incoming.products ORDER BY bld_no COLLATE BLD_NATURAL").fetchall()
        with conn:
            for row in rows:
                local_row = conn.execute("SELECT * FROM main.products WHERE bld_no = ?", (row["bld_no"],)).fetchone()
                if local_row is None:
                    new_count += 1
                elif _row_changed(local_row, row, columns) and _incoming_is_older(local_row, row):
                    conflict_count += 1
                    continue
                elif _row_changed(local_row, row, columns):
                    updated_count += 1
                else:
                    unchanged_count += 1
                    continue
                conn.execute(
                    f"""
                    INSERT INTO main.products ({column_sql})
                    VALUES ({placeholders})
                    ON CONFLICT(bld_no) DO UPDATE SET {assignments}
                    """,
                    [row[column] for column in insert_columns],
                )
            if deactivate_local_only:
                result = conn.execute(
                    """
                    UPDATE main.products
                    SET active = 0, updated_at = ?
                    WHERE active = 1
                      AND bld_no NOT IN (SELECT bld_no FROM incoming.products)
                    """,
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
                )
                deactivated_count = result.rowcount
            log_event(
                conn,
                "导入产品数据包",
                "product_sync",
                "products.sqlite3",
                f"新增 {new_count} 条，更新 {updated_count} 条，跳过无变化 {unchanged_count} 条，跳过包内旧数据 {conflict_count} 条，停用本机独有 {deactivated_count} 条；保留当前系统账号、API Key 和日志。",
                actor=actor_name(),
            )
    return new_count, updated_count, conflict_count, unchanged_count, deactivated_count


def _copy_media_dir(extracted_dir: Path, key: str, backup_dir: Path) -> int:
    relative, destination = MEDIA_DIRS[key]
    source = extracted_dir / relative
    if not source.exists():
        return 0
    count = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(source)
        target = destination / relative_path
        if target.exists():
            backup_path = backup_dir / relative / relative_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def _prepare_package(package_path: Path) -> tuple[tempfile.TemporaryDirectory, Path, dict[str, object]]:
    temporary = tempfile.TemporaryDirectory(prefix="bld-product-import-")
    extracted_dir = Path(temporary.name)
    try:
        _safe_extract_package(package_path, extracted_dir)
        product_db = _find_product_db(extracted_dir)
        _validate_product_db(product_db)
        return temporary, product_db, _read_manifest(extracted_dir)
    except Exception:
        temporary.cleanup()
        raise


def register(app) -> None:
    @app.get("/product-data-sync")
    @permission_required("sync_product_data")
    def product_data_sync():
        return render_template("product_data_sync.html", preview=None)

    @app.post("/product-data-sync/export")
    @permission_required("sync_product_data")
    def export_product_data_package():
        include_drawings = request.form.get("include_drawings") == "1"
        include_images = request.form.get("include_images") == "1"
        try:
            output_path = _export_package(include_drawings=include_drawings, include_images=include_images)
            with connect(DB_PATH) as conn:
                log_event(
                    conn,
                    "导出产品数据包",
                    "product_sync",
                    output_path.name,
                    f"包含图纸：{'是' if include_drawings else '否'}；包含图片：{'是' if include_images else '否'}",
                    actor=actor_name(),
                )
                conn.commit()
        except Exception as exc:
            flash(f"产品数据包导出失败：{exc}", "error")
            return redirect(url_for("product_data_sync"))
        return send_file(output_path, as_attachment=True)

    @app.post("/product-data-sync/import/preview")
    @permission_required("sync_product_data")
    def preview_product_data_package():
        file = request.files.get("package")
        if not file or not file.filename:
            flash("请选择产品数据包。", "error")
            return redirect(url_for("product_data_sync"))
        if not file.filename.endswith(PACKAGE_SUFFIX):
            flash("产品数据包必须是 .tar.gz 文件。", "error")
            return redirect(url_for("product_data_sync"))

        upload_path = user_upload_path(file.filename, prefix="product-data")
        file.save(upload_path)
        include_drawings = request.form.get("include_drawings") == "1"
        include_images = request.form.get("include_images") == "1"
        try:
            temporary, product_db, manifest = _prepare_package(upload_path)
            try:
                diff = _diff_products(product_db)
                preview = {
                    "package_path": str(upload_path),
                    "package_name": file.filename,
                    "manifest": manifest,
                    "diff": diff,
                    "include_drawings": include_drawings,
                    "include_images": include_images,
                    "media_counts": {
                        "drawings": _media_file_count(Path(temporary.name), "drawings"),
                        "product_images": _media_file_count(Path(temporary.name), "product_images"),
                    },
                }
            finally:
                temporary.cleanup()
        except Exception as exc:
            flash(f"产品数据包读取失败：{exc}", "error")
            return redirect(url_for("product_data_sync"))
        return render_template("product_data_sync.html", preview=preview)

    @app.post("/product-data-sync/import/apply")
    @permission_required("sync_product_data")
    def apply_product_data_package():
        package_path = _package_upload_path()
        if not package_path:
            flash("产品数据包路径无效，请重新上传预览。", "error")
            return redirect(url_for("product_data_sync"))
        include_drawings = request.form.get("include_drawings") == "1"
        include_images = request.form.get("include_images") == "1"
        deactivate_local_only = request.form.get("deactivate_local_only") == "1"
        backup_dir = DATA_DIR / "local-backups" / f"before-product-data-sync-{_now_label()}"
        try:
            with import_lock(actor_name(), "产品数据包导入"):
                backup_dir.mkdir(parents=True, exist_ok=True)
                for path in (DB_PATH, DB_PATH.with_name(f"{DB_PATH.name}-wal"), DB_PATH.with_name(f"{DB_PATH.name}-shm")):
                    if path.exists():
                        shutil.copy2(path, backup_dir / path.name)
                temporary, product_db, _manifest = _prepare_package(package_path)
                try:
                    new_count, updated_count, conflict_count, unchanged_count, deactivated_count = _apply_products(
                        product_db,
                        deactivate_local_only=deactivate_local_only,
                    )
                    copied_drawings = _copy_media_dir(Path(temporary.name), "drawings", backup_dir) if include_drawings else 0
                    copied_images = _copy_media_dir(Path(temporary.name), "product_images", backup_dir) if include_images else 0
                    with connect(DB_PATH) as conn:
                        log_event(
                            conn,
                            "应用产品数据包媒体",
                            "product_sync",
                            package_path.name,
                            f"复制图纸 {copied_drawings} 个；复制图片 {copied_images} 个；备份：{backup_dir}",
                            actor=actor_name(),
                        )
                        conn.commit()
                finally:
                    temporary.cleanup()
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("product_data_sync"))
        except Exception as exc:
            flash(f"产品数据包导入失败：{exc}", "error")
            return redirect(url_for("product_data_sync"))
        flash(
            f"产品数据导入完成：新增 {new_count} 条，更新 {updated_count} 条，跳过无变化 {unchanged_count} 条，跳过包内旧数据 {conflict_count} 条，停用本机独有 {deactivated_count} 条。",
            "success",
        )
        return redirect(url_for("product_data_sync"))
