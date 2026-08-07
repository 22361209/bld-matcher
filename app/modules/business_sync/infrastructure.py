from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.database import connect
from app.platform.audit_store import log_event
from app.modules.products.brand_normalization import canonicalize_brands
from app.modules.products.option_values import register_product_option_values
from app.platform.sync_identity import material_match_key, quote_match_key, stable_sync_id


PACKAGE_SUFFIX = ".tar.gz"
PACKAGE_TYPE = "bld_business_data"
PACKAGE_VERSION = 3
SUPPORTED_PACKAGE_VERSIONS = frozenset({1, 2, 3})
MAX_MEDIA_FILE_SIZE = 512 * 1024 * 1024
MAX_PACKAGE_METADATA_SIZE = 64 * 1024 * 1024
MAX_PACKAGE_MEMBER_COUNT = 10_000
MAX_PACKAGE_TOTAL_SIZE = 1024 * 1024 * 1024
MEDIA_DIRECTORIES = {
    "drawings": "data/drawings",
    "product_images": "data/product_images",
    "material_drawings": "data/material_drawings",
}
MEDIA_DATASETS = {
    "drawings": "products",
    "product_images": "products",
    "material_drawings": "materials",
}
DATASETS = {
    "customers": ("customers", "sync_id", "客户"),
    "products": ("products", "bld_no", "产品目录"),
    "quotes": ("quote_records", "sync_id", "报价记录"),
    "tubes": ("tube_items", "code", "管件资料"),
    "materials": ("material_items", "sync_id", "材料明细"),
}
FIELD_LABELS = {
    "active": "状态", "bld_no": "BLD NO.", "blank_length_text": "毛坯管长度", "borrowed_from": "借用编号",
    "car": "车型", "category": "类别", "code": "编号", "consumption_mm": "消耗长度", "currency": "币种",
    "customer_name": "客户", "customer_product_code": "客户产品编号", "inner_diameter_mm": "内径", "item": "产品名称",
    "length": "长度", "model": "母件编码", "models": "适用车型", "moq": "起订量", "name": "客户名称", "note": "备注",
    "oe_no_1": "OE 号 1", "oe_no_2": "OE 号 2", "outer_diameter_mm": "外径", "part": "零件",
    "pieces": "下料只数", "price": "报价", "price_cny": "价格", "product_model": "产品型号", "product_status": "产品状态",
    "purchase_base": "采购基数", "quote_date": "报价日期", "quote_no": "报价单号", "quoted_by": "报价人", "remark": "备注", "series": "系列",
    "source": "来源", "source_row": "来源行", "source_sheet": "来源工作表", "source_text": "来源内容", "source_type": "来源类型",
    "spec_text": "规格", "tax_price": "含税价", "net_price": "未税价", "thickness": "厚度", "tolerance_mm": "公差",
    "tube_type": "产品名称", "weight_kg": "重量", "width": "宽度",
}
COMPARISON_EXCLUDED_COLUMNS = {"sync_id", "attachment_path", "created_at", "updated_at", "version"}
LOCAL_MEDIA_COLUMNS = {
    # 负责人账号属于设备本地身份，不随客户主数据跨设备覆盖。
    "customers": {"owner_username"},
    "products": {"image_path", "image_path_2", "image_path_3", "image_path_4", "image_path_5", "drawing_path", "drawing_original_name", "drawing_updated_at"},
    # customer_id 是每台设备的本地主键，跨设备同步时按 customer_name 重新解析。
    "quotes": {"attachment_path", "customer_id"},
}


def _media_relative_path(member_name: str, key: str) -> Path:
    prefix = f"{MEDIA_DIRECTORIES[key]}/"
    if not member_name.startswith(prefix):
        raise ValueError("业务数据包包含不安全的媒体路径。")
    raw_relative = member_name.removeprefix(prefix)
    raw_parts = raw_relative.split("/")
    posix_path = PurePosixPath(raw_relative)
    windows_path = PureWindowsPath(raw_relative)
    if (
        not raw_relative
        or "\\" in raw_relative
        or posix_path.is_absolute()
        or bool(windows_path.drive)
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError("业务数据包包含不安全的媒体路径。")
    if key == "material_drawings" and (len(raw_parts) != 1 or posix_path.suffix != ".pdf"):
        raise ValueError("业务数据包中的物料图纸必须是根目录下的小写 .pdf 文件。")
    return Path(*raw_parts)


def _normalized_media_target(relative: Path) -> str:
    return unicodedata.normalize("NFC", relative.as_posix()).casefold()


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})") if row["name"] != "id"]


def _changed(key: str, local: sqlite3.Row | None, incoming: dict[str, object], columns: list[str]) -> bool:
    return local is None or any(local[column] != incoming.get(column) for column in columns if column not in LOCAL_MEDIA_COLUMNS.get(key, set()))


def _older(local: sqlite3.Row, incoming: dict[str, object]) -> bool:
    return str(incoming.get("updated_at") or "") < str(local["updated_at"] or "")


def _status(key: str, local: sqlite3.Row | None, incoming: dict[str, object], columns: list[str]) -> str:
    if local is None:
        return "new"
    if not _changed(key, local, incoming, columns):
        return "unchanged"
    if key == "quotes" or _older(local, incoming):
        return "conflict"
    return "updated"


def _unresolved_customers(connection: sqlite3.Connection, payload: dict[str, list[dict[str, object]]]) -> list[str]:
    """报价行里本机 customers 表和数据包 customers 数据集都不存在的客户名。"""

    names = {str(row.get("customer_name") or "").strip() for row in payload.get("quotes", [])}
    names.discard("")
    if not names:
        return []
    local = {str(row["name"]).upper() for row in connection.execute("SELECT name FROM customers").fetchall()}
    incoming = {str(row.get("name") or "").strip().upper() for row in payload.get("customers", [])}
    return sorted(name for name in names if name.upper() not in local and name.upper() not in incoming)


def _customer_name_key(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normalize_quote_customer_links(
    connection: sqlite3.Connection,
    *,
    preexisting_quote_ids: set[int],
    imported_quote_ids: set[int],
) -> None:
    """Keep quote customer names aligned with device-local customer identities."""

    customers = connection.execute("SELECT id, name FROM customers ORDER BY id").fetchall()
    customers_by_id = {int(row["id"]): str(row["name"]) for row in customers}
    customers_by_name: dict[str, list[tuple[int, str]]] = {}
    for row in customers:
        customer_id = int(row["id"])
        customer_name = str(row["name"])
        customers_by_name.setdefault(_customer_name_key(customer_name), []).append((customer_id, customer_name))

    quotes = connection.execute(
        "SELECT id, customer_id, customer_name FROM quote_records ORDER BY id"
    ).fetchall()
    for row in quotes:
        quote_id = int(row["id"])
        raw_customer_id = row["customer_id"]
        try:
            local_customer_id = int(raw_customer_id) if raw_customer_id is not None else None
        except (TypeError, ValueError):
            local_customer_id = None

        if quote_id in imported_quote_ids:
            matches = customers_by_name.get(_customer_name_key(row["customer_name"]), [])
            if len(matches) != 1:
                raise ValueError(f"报价客户 {row['customer_name']} 无法映射到本机客户。")
            target_customer_id, target_customer_name = matches[0]
        else:
            canonical_name = customers_by_id.get(local_customer_id) if local_customer_id is not None else None
            if canonical_name is not None:
                target_customer_id = local_customer_id
                target_customer_name = canonical_name
            else:
                matches = customers_by_name.get(_customer_name_key(row["customer_name"]), [])
                if len(matches) != 1:
                    if raw_customer_id is not None:
                        if quote_id in preexisting_quote_ids:
                            connection.execute(
                                """
                                UPDATE quote_records
                                SET customer_id = NULL, version = version + 1,
                                    updated_at = datetime('now','localtime')
                                WHERE id = ?
                                """,
                                (quote_id,),
                            )
                        else:
                            connection.execute(
                                "UPDATE quote_records SET customer_id = NULL WHERE id = ?",
                                (quote_id,),
                            )
                    continue
                target_customer_id, target_customer_name = matches[0]

        if raw_customer_id != target_customer_id or row["customer_name"] != target_customer_name:
            if quote_id in imported_quote_ids:
                connection.execute(
                    "UPDATE quote_records SET customer_id = ?, customer_name = ? WHERE id = ?",
                    (target_customer_id, target_customer_name, quote_id),
                )
            elif quote_id in preexisting_quote_ids:
                connection.execute(
                    """
                    UPDATE quote_records
                    SET customer_id = ?, customer_name = ?, version = version + 1,
                        updated_at = datetime('now','localtime')
                    WHERE id = ?
                    """,
                    (target_customer_id, target_customer_name, quote_id),
                )
            else:
                connection.execute(
                    "UPDATE quote_records SET customer_id = ?, customer_name = ? WHERE id = ?",
                    (target_customer_id, target_customer_name, quote_id),
                )


def _write_values(
    key: str,
    write_columns: list[str],
    incoming: dict[str, object],
    local_row: sqlite3.Row | None,
) -> list[object]:
    values = [incoming[column] for column in write_columns]
    if key == "quotes" and local_row is not None and "version" in write_columns:
        values[write_columns.index("version")] = int(local_row["version"] or 0) + 1
    return values


def _package_digest(package_path: Path) -> str:
    digest = hashlib.sha256()
    with package_path.open("rb") as package:
        while chunk := package.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _state_token(connection: sqlite3.Connection, package_path: Path, datasets: tuple[str, ...]) -> str:
    state: dict[str, list[list[object]]] = {}
    for key in datasets:
        table, identity, _label = DATASETS[key]
        columns = _columns(connection, table)
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY {identity}"
        ).fetchall()
        state[key] = [[row[column] for column in columns] for row in rows]
    payload = json.dumps(
        {"package": _package_digest(package_path), "state": state},
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_row(key: str, local_rows: list[sqlite3.Row], incoming: dict[str, object]) -> sqlite3.Row | None:
    key_factory = quote_match_key if key == "quotes" else material_match_key
    candidates = [row for row in local_rows if key_factory(dict(row)) == key_factory(incoming)]
    return candidates[0] if len(candidates) == 1 else None


def _equivalent_without_sync(key: str, local: sqlite3.Row, incoming: dict[str, object], columns: list[str]) -> bool:
    ignored = {"sync_id", "created_at", "updated_at", "version"} | LOCAL_MEDIA_COLUMNS.get(key, set())
    return all(local[column] == incoming.get(column) for column in columns if column not in ignored)


def _incoming_status(
    key: str,
    local: dict[str, sqlite3.Row],
    local_rows: list[sqlite3.Row],
    incoming: dict[str, object],
    columns: list[str],
) -> tuple[str, sqlite3.Row | None, bool]:
    identity = DATASETS[key][1]
    local_row = local.get(str(incoming[identity]))
    if local_row is not None:
        return _status(key, local_row, incoming, columns), local_row, False
    if key not in {"quotes", "materials"}:
        return "new", None, False
    candidate = _candidate_row(key, local_rows, incoming)
    if candidate is None:
        return "new", None, False
    if _equivalent_without_sync(key, candidate, incoming, columns):
        return "updated", candidate, True
    return "conflict", candidate, False


def _preview_label(key: str, incoming: dict[str, object]) -> str:
    if key == "customers":
        return str(incoming.get("name") or "—")
    if key == "materials":
        fields = ("model", "code", "category", "car", "part", "spec_text")
        return " · ".join(str(incoming.get(field) or "—") for field in fields)
    if key == "quotes":
        fields = ("customer_name", "bld_no", "customer_product_code", "quote_date")
        return " · ".join(str(incoming.get(field) or "—") for field in fields)
    return str(incoming.get(DATASETS[key][1]) or "—")


def _display_value(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def _comparison_fields(key: str, local: sqlite3.Row, incoming: dict[str, object], columns: list[str]) -> list[dict[str, str]]:
    return [
        {"label": FIELD_LABELS.get(column, column), "before": _display_value(local[column]), "after": _display_value(incoming.get(column))}
        for column in columns
        if column not in COMPARISON_EXCLUDED_COLUMNS | LOCAL_MEDIA_COLUMNS.get(key, set()) and local[column] != incoming.get(column)
    ]


def _all_comparison_fields(key: str, local: sqlite3.Row, incoming: dict[str, object], columns: list[str]) -> list[dict[str, str | bool]]:
    return [
        {
            "label": FIELD_LABELS.get(column, column),
            "before": _display_value(local[column]),
            "after": _display_value(incoming.get(column)),
            "changed": local[column] != incoming.get(column),
        }
        for column in columns
        if column not in COMPARISON_EXCLUDED_COLUMNS | LOCAL_MEDIA_COLUMNS.get(key, set())
    ]


class BusinessSyncRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        drawing_dir: Path | None = None,
        image_dir: Path | None = None,
        material_drawing_dir: Path | None = None,
    ) -> None:
        self.database_path = database_path
        self.media_dirs = {
            "drawings": drawing_dir,
            "product_images": image_dir,
            "material_drawings": material_drawing_dir,
        }

    def export(
        self,
        *,
        output_path: Path,
        selected: tuple[str, ...],
        actor: str,
        include_drawings: bool = False,
        include_images: bool = False,
        include_material_drawings: bool = False,
    ) -> Path:
        payload: dict[str, list[dict[str, object]]] = {}
        with connect(self.database_path) as connection:
            for key in selected:
                table, _identity, _label = DATASETS[key]
                columns = _columns(connection, table)
                rows = [dict(row) for row in connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()]
                for row in rows:
                    for column in LOCAL_MEDIA_COLUMNS.get(key, set()):
                        row[column] = ""
                payload[key] = rows
        includes = {
            "drawings": bool("products" in selected and include_drawings),
            "product_images": bool("products" in selected and include_images),
            "material_drawings": bool("materials" in selected and include_material_drawings),
        }
        manifest = {
            "package_type": PACKAGE_TYPE,
            "version": PACKAGE_VERSION,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "datasets": list(selected),
            "media": {**includes, "files": {key: 0 for key in MEDIA_DIRECTORIES}},
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        published_identity: tuple[int, int, int, int] | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="bld-business-sync-") as directory:
                root = Path(directory)
                (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                (root / "data.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
                with tarfile.open(temporary, "w:gz") as archive:
                    archive.add(root / "data.json", arcname="data.json")
                    media = manifest["media"]
                    if not isinstance(media, dict):
                        raise RuntimeError("Business package manifest is invalid.")
                    files = media["files"]
                    if not isinstance(files, dict):
                        raise RuntimeError("Business package media manifest is invalid.")
                    for key, include in includes.items():
                        if include:
                            files[key] = self._add_media_directory(archive, key)
                    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                    archive.add(root / "manifest.json", arcname="manifest.json")
            self.read(temporary)
            os.replace(temporary, output_path)
            stat = output_path.stat()
            published_identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        finally:
            temporary.unlink(missing_ok=True)
        try:
            media = manifest["media"]
            if not isinstance(media, dict) or not isinstance(media.get("files"), dict):
                raise RuntimeError("Business package media manifest is invalid.")
            files = media["files"]
            media_labels = {
                "drawings": "产品图纸",
                "product_images": "产品图片",
                "material_drawings": "物料图纸",
            }
            included_media = [
                f"{media_labels[key]} {files[key]} 个"
                for key, include in includes.items()
                if include
            ]
            detail = f"包含：{'、'.join(DATASETS[key][2] for key in selected)}；媒体：{'、'.join(included_media) if included_media else '无'}"
            with connect(self.database_path) as connection:
                log_event(
                    connection,
                    "导出业务数据包",
                    "business_sync",
                    output_path.name,
                    detail,
                    actor=actor,
                )
                connection.commit()
        except Exception:
            try:
                current = output_path.stat()
                current_identity = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                if published_identity is not None and current_identity == published_identity:
                    output_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return output_path

    def _add_media_directory(self, archive: tarfile.TarFile, key: str) -> int:
        source = self.media_dirs[key]
        if source is None or not source.is_dir():
            return 0
        source_root = source.resolve()
        count = 0
        normalized_targets: set[str] = set()
        paths = source.iterdir() if key == "material_drawings" else source.rglob("*")
        for path in sorted(paths):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                relative = path.relative_to(source)
                resolved = path.resolve(strict=True)
                stat = path.stat()
            except (OSError, ValueError):
                continue
            if stat.st_nlink > 1:
                continue
            relative_parents = [source.joinpath(*relative.parts[:index]) for index in range(1, len(relative.parts))]
            if source_root not in resolved.parents or any(parent.is_symlink() for parent in relative_parents):
                continue
            if key == "material_drawings" and (len(relative.parts) != 1 or path.suffix != ".pdf"):
                continue
            normalized_target = _normalized_media_target(relative)
            if normalized_target in normalized_targets:
                raise ValueError("业务数据媒体目录包含会指向同一目标的文件。")
            normalized_targets.add(normalized_target)
            arcname = PurePosixPath(MEDIA_DIRECTORIES[key], *relative.parts).as_posix()
            archive.add(path, arcname=arcname)
            count += 1
        return count

    @staticmethod
    def read(package_path: Path) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
        try:
            with tarfile.open(package_path, "r:gz") as archive:
                raw_members = archive.getmembers()
                members = {member.name: member for member in raw_members}
                if len(members) != len(raw_members) or not {"manifest.json", "data.json"}.issubset(members):
                    raise ValueError("业务数据包格式或文件大小无效。")
                if (
                    len(raw_members) > MAX_PACKAGE_MEMBER_COUNT
                    or sum(member.size for member in raw_members) > MAX_PACKAGE_TOTAL_SIZE
                    or members["manifest.json"].size > MAX_PACKAGE_METADATA_SIZE
                    or members["data.json"].size > MAX_PACKAGE_METADATA_SIZE
                ):
                    raise ValueError("业务数据包格式或文件大小无效。")
                if any(
                    not member.isfile()
                    or member.issym()
                    or member.islnk()
                    or member.size < 0
                    or member.size > MAX_MEDIA_FILE_SIZE
                    for member in raw_members
                ):
                    raise ValueError("业务数据包格式或文件大小无效。")
                manifest_file = archive.extractfile(members["manifest.json"])
                data_file = archive.extractfile(members["data.json"])
                if manifest_file is None or data_file is None:
                    raise ValueError("业务数据包内容不完整。")
                manifest = json.loads(manifest_file.read().decode("utf-8"))
                payload = json.loads(data_file.read().decode("utf-8"))
        except (OSError, tarfile.TarError, json.JSONDecodeError) as exc:
            raise ValueError("业务数据包无法读取。") from exc
        if not isinstance(manifest, dict) or manifest.get("package_type") != PACKAGE_TYPE or not isinstance(payload, dict):
            raise ValueError("不是受支持的业务数据包。")
        version = manifest.get("version")
        if type(version) is not int or version not in SUPPORTED_PACKAGE_VERSIONS:
            raise ValueError("业务数据包版本不受支持，请先升级系统后再导入。")
        raw_datasets = manifest.get("datasets", [])
        if (
            not isinstance(raw_datasets, list)
            or any(not isinstance(key, str) or key not in DATASETS for key in raw_datasets)
            or len(set(raw_datasets)) != len(raw_datasets)
        ):
            raise ValueError("业务数据包缺少可导入的数据集。")
        selected = tuple(raw_datasets)
        if not selected or set(payload) != set(selected) or any(not isinstance(payload.get(key), list) for key in selected):
            raise ValueError("业务数据包缺少可导入的数据集。")
        media = manifest.get("media", {})
        if not isinstance(media, dict):
            raise ValueError("业务数据包媒体信息无效。")
        known_media_fields = set(MEDIA_DIRECTORIES) | {"files"}
        if any(key not in known_media_fields for key in media):
            raise ValueError("业务数据包媒体信息无效。")
        if any(key in media and type(media[key]) is not bool for key in MEDIA_DIRECTORIES):
            raise ValueError("业务数据包媒体信息无效。")
        enabled_media = {key for key in MEDIA_DIRECTORIES if media.get(key) is True}
        if "material_drawings" in enabled_media and version < 3:
            raise ValueError("该版本业务数据包不支持物料图纸。")
        if any(MEDIA_DATASETS[key] not in selected for key in enabled_media):
            raise ValueError("业务数据包媒体与数据集不匹配。")
        declared_files = media.get("files", {})
        if not isinstance(declared_files, dict) or any(key not in MEDIA_DIRECTORIES for key in declared_files):
            raise ValueError("业务数据包媒体文件计数无效。")
        actual_files = {key: 0 for key in MEDIA_DIRECTORIES}
        normalized_targets = {key: set() for key in MEDIA_DIRECTORIES}
        for member in raw_members:
            if member.name in {"manifest.json", "data.json"}:
                continue
            media_key = next(
                (key for key, directory in MEDIA_DIRECTORIES.items() if member.name.startswith(f"{directory}/")),
                None,
            )
            if media_key is None or media_key not in enabled_media:
                raise ValueError("业务数据包包含未声明的文件。")
            relative = _media_relative_path(member.name, media_key)
            normalized_target = _normalized_media_target(relative)
            if normalized_target in normalized_targets[media_key]:
                raise ValueError("业务数据包包含会指向同一目标的媒体文件。")
            normalized_targets[media_key].add(normalized_target)
            actual_files[media_key] += 1
        for key, actual_count in actual_files.items():
            declared_count = declared_files.get(key, 0)
            if type(declared_count) is not int or declared_count < 0 or declared_count != actual_count:
                raise ValueError("业务数据包媒体文件计数与实际内容不一致。")
        for key in selected:
            identity = DATASETS[key][1]
            seen: set[str] = set()
            duplicates: list[str] = []
            for row in payload[key]:
                if not isinstance(row, dict) or not str(row.get(identity) or "").strip():
                    raise ValueError(f"{DATASETS[key][2]}包含无效编号。")
                value = str(row[identity])
                if value in seen:
                    duplicates.append(value)
                seen.add(value)
            if duplicates:
                raise ValueError(f"{DATASETS[key][2]}包含重复编号：{'、'.join(duplicates[:10])}")
        return manifest, {key: payload[key] for key in selected}

    def preview(self, package_path: Path) -> dict[str, object]:
        manifest, payload = self.read(package_path)
        summary: dict[str, dict[str, object]] = {}
        with connect(self.database_path) as connection:
            for key, incoming_rows in payload.items():
                table, identity, label = DATASETS[key]
                columns = _columns(connection, table)
                if any(identity not in row or any(column not in row for column in columns) for row in incoming_rows if isinstance(row, dict)):
                    raise ValueError(f"{label}字段与当前系统不一致，请先升级后再导入。")
                local_rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                local = {str(row[identity]): row for row in local_rows}
                counts = {"new": 0, "updated": 0, "conflict": 0, "unchanged": 0}
                rows: list[dict[str, object]] = []
                conflicts: list[dict[str, object]] = []
                for raw_incoming in incoming_rows:
                    incoming = self._normalized_incoming(key, raw_incoming)
                    if not isinstance(incoming, dict):
                        raise ValueError(f"{label}包含无效记录。")
                    status, local_row, _adopt_sync_id = _incoming_status(key, local, local_rows, incoming, columns)
                    counts[status] += 1
                    if status == "conflict":
                        conflicts.append({
                            "key": str(incoming[identity]),
                            "label": _preview_label(key, incoming),
                            "fields": _comparison_fields(key, local_row, incoming, columns) if local_row else [],
                            "all_fields": _all_comparison_fields(key, local_row, incoming, columns) if local_row else [],
                            "local_updated_at": local_row["updated_at"] if local_row else "",
                            "incoming_updated_at": incoming.get("updated_at", ""),
                        })
                    if status != "unchanged":
                        rows.append({"status": status, "key": str(incoming[identity]), "label": _preview_label(key, incoming), "local_updated_at": local_row["updated_at"] if local_row else "", "incoming_updated_at": incoming.get("updated_at", "")})
                if key == "products":
                    incoming_ids = {str(row[identity]) for row in incoming_rows if isinstance(row, dict)}
                    counts["local_only"] = sum(1 for row in local_rows if str(row[identity]) not in incoming_ids)
                summary[key] = {"label": label, "counts": counts, "rows": rows, "conflicts": conflicts}
            token = _state_token(connection, package_path, tuple(payload))
            unresolved_customers = _unresolved_customers(connection, payload)
            customer_options = (
                [str(row["name"]) for row in connection.execute("SELECT name FROM customers ORDER BY name COLLATE NOCASE").fetchall()]
                if unresolved_customers
                else []
            )
        return {
            "manifest": manifest,
            "summary": summary,
            "token": token,
            "unresolved_customers": unresolved_customers,
            "customer_options": customer_options,
            "media": self._media_summary(manifest),
        }

    @staticmethod
    def _normalized_incoming(key: str, incoming: object) -> dict[str, object]:
        if not isinstance(incoming, dict):
            raise ValueError(f"{DATASETS[key][2]}包含无效记录。")
        normalized = dict(incoming)
        if key == "products":
            normalized["series"] = canonicalize_brands(normalized.get("series"))
        return normalized

    @staticmethod
    def _media_summary(manifest: dict[str, object]) -> dict[str, object]:
        media = manifest.get("media", {})
        if not isinstance(media, dict):
            media = {}
        files = media.get("files", {})
        if not isinstance(files, dict):
            files = {}
        normalized_files: dict[str, int] = {}
        for key in MEDIA_DIRECTORIES:
            value = files.get(key, 0)
            normalized_files[key] = value if type(value) is int and value >= 0 else 0
        return {
            "drawings": media.get("drawings") is True,
            "product_images": media.get("product_images") is True,
            "material_drawings": media.get("material_drawings") is True,
            "files": normalized_files,
        }

    @staticmethod
    def _resolve_quote_customers(connection: sqlite3.Connection, payload: dict[str, list[dict[str, object]]], mappings: dict[str, str | None]) -> None:
        unresolved = _unresolved_customers(connection, payload)
        if not unresolved:
            return
        missing = [name for name in unresolved if name not in mappings]
        if missing:
            raise ValueError("报价包含本机未登记的客户，请为每个客户选择新建或映射：" + "、".join(missing[:10]))
        for name in unresolved:
            target = mappings.get(name)
            if target is None:
                connection.execute(
                    "INSERT OR IGNORE INTO customers (name, sync_id) VALUES (?, ?)",
                    (name, stable_sync_id("customer", name.upper(), 1)),
                )
                continue
            local = connection.execute(
                "SELECT name FROM customers WHERE name = ? COLLATE NOCASE",
                (target,),
            ).fetchone()
            if local is None:
                raise ValueError(f"映射目标客户 {target} 不存在，请重新上传预览。")
            canonical = str(local["name"])
            for row in payload["quotes"]:
                if str(row.get("customer_name") or "").strip() == name:
                    row["customer_name"] = canonical

    def apply(
        self,
        package_path: Path,
        *,
        backup_path: Path,
        actor: str,
        expected_token: str,
        selected_conflicts: dict[str, set[str]],
        customer_mappings: dict[str, str | None] | None = None,
        include_drawings: bool = False,
        include_images: bool = False,
        include_material_drawings: bool = False,
        deactivate_local_only: bool = False,
    ) -> dict[str, dict[str, int]]:
        manifest, payload = self.read(package_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(backup_path)
        try:
            with connect(self.database_path) as source:
                source.backup(backup)
            backup.commit()
        finally:
            backup.close()
        result: dict[str, dict[str, int]] = {}
        media_changes: list[tuple[Path, Path | None]] = []
        media_backup_root = backup_path.with_name(f"{backup_path.name}.media")
        connection = connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if _state_token(connection, package_path, tuple(payload)) != expected_token:
                raise ValueError("预览后数据包或本机数据已变化，请重新上传预览。")
            media_requests = {
                "drawings": include_drawings,
                "product_images": include_images,
                "material_drawings": include_material_drawings,
            }
            for key, requested in media_requests.items():
                if MEDIA_DATASETS[key] in payload:
                    self._copy_media(package_path, manifest, key, requested, media_backup_root, media_changes)
            preexisting_quote_ids = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM quote_records").fetchall()
            }
            imported_quote_ids: set[int] = set()
            self._resolve_quote_customers(connection, payload, customer_mappings or {})
            for key, incoming_rows in payload.items():
                table, identity, _label = DATASETS[key]
                columns = _columns(connection, table)
                write_columns = [column for column in columns if column not in LOCAL_MEDIA_COLUMNS.get(key, set())]
                insert_sql = ", ".join(write_columns)
                placeholders = ", ".join("?" for _ in write_columns)
                updates = ", ".join(f"{column}=excluded.{column}" for column in write_columns if column != identity)
                local_rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                local = {str(row[identity]): row for row in local_rows}
                counts = {"new": 0, "updated": 0, "conflict": 0, "unchanged": 0}
                for raw_incoming in incoming_rows:
                    incoming = self._normalized_incoming(key, raw_incoming)
                    status, local_row, adopt_sync_id = _incoming_status(key, local, local_rows, incoming, columns)
                    selected_conflict = status == "conflict" and str(incoming[identity]) in selected_conflicts.get(key, set())
                    if selected_conflict:
                        status = "updated"
                    counts[status] += 1
                    if status in {"unchanged", "conflict"}:
                        continue
                    if selected_conflict and local_row is not None and str(local_row[identity]) != str(incoming[identity]):
                        assignments = ", ".join(f"{column} = ?" for column in write_columns)
                        connection.execute(
                            f"UPDATE {table} SET {assignments} WHERE id = ?",
                            _write_values(key, write_columns, incoming, local_row) + [local_row["id"]],
                        )
                        if key == "quotes":
                            imported_quote_ids.add(int(local_row["id"]))
                        continue
                    if adopt_sync_id and local_row is not None:
                        connection.execute(f"UPDATE {table} SET sync_id = ? WHERE id = ?", (incoming[identity], local_row["id"]))
                        continue
                    connection.execute(
                        f"INSERT INTO {table} ({insert_sql}) VALUES ({placeholders}) "
                        f"ON CONFLICT({identity}) DO UPDATE SET {updates}",
                        _write_values(key, write_columns, incoming, local_row),
                    )
                    if key == "products":
                        register_product_option_values(
                            connection,
                            series=str(incoming.get("series") or ""),
                            item=str(incoming.get("item") or ""),
                            product_status=str(incoming.get("product_status") or ""),
                        )
                    if key == "quotes":
                        imported = connection.execute(
                            f"SELECT id FROM {table} WHERE {identity} = ?",
                            (incoming[identity],),
                        ).fetchone()
                        if imported is None:
                            raise RuntimeError("Imported quote could not be reloaded.")
                        imported_quote_ids.add(int(imported["id"]))
                result[key] = counts
            if deactivate_local_only and "products" in payload:
                incoming_bld = {str(row["bld_no"]) for row in payload["products"]}
                placeholders = ", ".join("?" for _ in incoming_bld) or "''"
                cursor = connection.execute(
                    f"UPDATE products SET active = 0, updated_at = ? WHERE active = 1 AND bld_no NOT IN ({placeholders})",
                    [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *sorted(incoming_bld)],
                )
                result["products"]["deactivated"] = int(cursor.rowcount)
            if {"customers", "quotes"}.intersection(payload):
                _normalize_quote_customer_links(
                    connection,
                    preexisting_quote_ids=preexisting_quote_ids,
                    imported_quote_ids=imported_quote_ids,
                )
            log_event(connection, "导入业务数据包", "business_sync", package_path.name, "；".join(f"{DATASETS[key][2]}新增 {counts['new']}、更新 {counts['updated']}、冲突 {counts['conflict']}" for key, counts in result.items()), actor=actor)
            connection.commit()
        except Exception:
            connection.rollback()
            self._restore_media(media_changes)
            raise
        finally:
            connection.close()
        return result

    def _copy_media(
        self,
        package_path: Path,
        manifest: dict[str, object],
        key: str,
        requested: bool,
        backup_root: Path,
        changes: list[tuple[Path, Path | None]],
    ) -> None:
        if not requested or not self._media_summary(manifest).get(key):
            return
        destination = self.media_dirs[key]
        if destination is None:
            raise ValueError("当前系统未配置业务数据媒体目录。")
        existing_targets = self._existing_media_targets(destination, key)
        prefix = f"{MEDIA_DIRECTORIES[key]}/"
        with tarfile.open(package_path, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.startswith(prefix):
                    continue
                relative = _media_relative_path(member.name, key)
                normalized_target = _normalized_media_target(relative)
                target_relative = existing_targets.get(normalized_target, relative)
                target = self._safe_media_target(destination, target_relative)
                backup = self._safe_media_target(backup_root / key, target_relative) if target.exists() else None
                if backup is not None:
                    self._atomic_copy(target, backup)
                changes.append((target, backup))
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("业务数据包媒体文件无法读取。")
                self._atomic_copy_stream(source, target)
                existing_targets[normalized_target] = target_relative

    @staticmethod
    def _existing_media_targets(destination: Path, key: str) -> dict[str, Path]:
        if not destination.exists():
            return {}
        if not destination.is_dir():
            raise ValueError("业务数据媒体目标目录无效。")
        try:
            root = destination.resolve(strict=True)
            paths = destination.iterdir() if key == "material_drawings" else destination.rglob("*")
            targets: dict[str, Path] = {}
            for path in sorted(paths):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(destination)
                resolved = path.resolve(strict=True)
                if root not in resolved.parents:
                    continue
                normalized_target = _normalized_media_target(relative)
                existing = targets.get(normalized_target)
                if existing is not None and existing != relative:
                    raise ValueError("当前系统媒体目录包含仅大小写或 Unicode 形式不同的重复文件，请先整理后再导入。")
                targets[normalized_target] = relative
            return targets
        except ValueError:
            raise
        except (OSError, RuntimeError) as exc:
            raise ValueError("业务数据媒体目标目录无效。") from exc

    @staticmethod
    def _safe_media_target(destination: Path, relative: Path) -> Path:
        try:
            if destination.exists() and not destination.is_dir():
                raise ValueError("业务数据媒体目标目录无效。")
            root = destination.resolve(strict=False)
            candidate = destination.joinpath(relative)
            resolved_candidate = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ValueError("业务数据媒体目标路径无效。") from exc
        if resolved_candidate == root or root not in resolved_candidate.parents:
            raise ValueError("业务数据媒体目标路径越界。")

        current = destination
        for part in relative.parts[:-1]:
            current /= part
            try:
                if current.is_symlink():
                    resolved_parent = current.resolve(strict=False)
                    if resolved_parent != root and root not in resolved_parent.parents:
                        raise ValueError("业务数据媒体目标路径越界。")
                elif current.exists() and not current.is_dir():
                    raise ValueError("业务数据媒体目标路径包含非目录组件。")
            except (OSError, RuntimeError) as exc:
                raise ValueError("业务数据媒体目标路径无效。") from exc
        if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
            raise ValueError("业务数据媒体目标不能是目录、链接或特殊文件。")
        return candidate

    @staticmethod
    def _atomic_copy(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _atomic_copy_stream(source, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            os.replace(temporary, target)
        finally:
            source.close()
            temporary.unlink(missing_ok=True)

    def _restore_media(self, changes: list[tuple[Path, Path | None]]) -> None:
        for target, backup in reversed(changes):
            if backup is not None and backup.exists():
                self._atomic_copy(backup, target)
            else:
                target.unlink(missing_ok=True)
