from __future__ import annotations

import json
import os
import tarfile
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath

from app.database import connect
from app.platform.audit_store import log_event

from ._comparison import columns
from ._media_transaction import media_relative_path, normalized_media_target
from ._schema import (
    DATASETS,
    LOCAL_MEDIA_COLUMNS,
    MAX_MEDIA_FILE_SIZE,
    MAX_PACKAGE_MEMBER_COUNT,
    MAX_PACKAGE_METADATA_SIZE,
    MAX_PACKAGE_TOTAL_SIZE,
    MEDIA_DATASETS,
    MEDIA_DIRECTORIES,
    PACKAGE_TYPE,
    PACKAGE_VERSION,
    SUPPORTED_PACKAGE_VERSIONS,
)


Manifest = dict[str, object]
Payload = dict[str, list[dict[str, object]]]
PackageReader = Callable[[Path], tuple[Manifest, Payload]]
MediaAdder = Callable[[tarfile.TarFile, str], int]


def add_media_directory(
    archive: tarfile.TarFile,
    key: str,
    media_dirs: dict[str, Path | None],
) -> int:
    source = media_dirs[key]
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
        normalized_target = normalized_media_target(relative)
        if normalized_target in normalized_targets:
            raise ValueError("业务数据媒体目录包含会指向同一目标的文件。")
        normalized_targets.add(normalized_target)
        arcname = PurePosixPath(MEDIA_DIRECTORIES[key], *relative.parts).as_posix()
        archive.add(path, arcname=arcname)
        count += 1
    return count


def read_package(package_path: Path) -> tuple[Manifest, Payload]:
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
        relative = media_relative_path(member.name, media_key)
        normalized_target = normalized_media_target(relative)
        if normalized_target in normalized_targets[media_key]:
            raise ValueError("业务数据包包含会指向同一目标的媒体文件。")
        normalized_targets[media_key].add(normalized_target)
        actual_files[media_key] += 1
    for key, actual_count in actual_files.items():
        declared_count = declared_files.get(key, 0)
        if type(declared_count) is not int or declared_count < 0 or declared_count != actual_count:
            raise ValueError("业务数据包媒体文件计数与实际内容不一致。")
    typed_payload: Payload = {}
    for key in selected:
        identity = DATASETS[key][1]
        seen: set[str] = set()
        duplicates: list[str] = []
        rows = payload[key]
        for row in rows:
            if not isinstance(row, dict) or not str(row.get(identity) or "").strip():
                raise ValueError(f"{DATASETS[key][2]}包含无效编号。")
            value = str(row[identity])
            if value in seen:
                duplicates.append(value)
            seen.add(value)
        if duplicates:
            raise ValueError(f"{DATASETS[key][2]}包含重复编号：{'、'.join(duplicates[:10])}")
        typed_payload[key] = rows
    return manifest, typed_payload


def export_package(
    database_path: Path,
    *,
    output_path: Path,
    selected: tuple[str, ...],
    actor: str,
    include_drawings: bool,
    include_images: bool,
    include_material_drawings: bool,
    add_media_directory_fn: MediaAdder,
    read_package_fn: PackageReader,
) -> Path:
    payload: Payload = {}
    with connect(database_path) as connection:
        for key in selected:
            table, _identity, _label = DATASETS[key]
            record_columns = columns(connection, table)
            where_clause = " WHERE active = 1" if key == "products" else ""
            rows = [
                dict(row)
                for row in connection.execute(
                    f"SELECT {', '.join(record_columns)} FROM {table}{where_clause}"
                ).fetchall()
            ]
            for row in rows:
                for column in LOCAL_MEDIA_COLUMNS.get(key, set()):
                    row[column] = ""
            payload[key] = rows
    includes = {
        "drawings": bool("products" in selected and include_drawings),
        "product_images": bool("products" in selected and include_images),
        "material_drawings": bool("materials" in selected and include_material_drawings),
    }
    manifest: Manifest = {
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
                        files[key] = add_media_directory_fn(archive, key)
                (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                archive.add(root / "manifest.json", arcname="manifest.json")
        read_package_fn(temporary)
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
        detail = (
            f"包含：{'、'.join(DATASETS[key][2] for key in selected)}；"
            f"媒体：{'、'.join(included_media) if included_media else '无'}"
        )
        with connect(database_path) as connection:
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
