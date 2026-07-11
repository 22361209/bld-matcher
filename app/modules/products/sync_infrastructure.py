from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .sync_repository import SQLiteProductSyncRepository


PACKAGE_SUFFIX = ".tar.gz"
PRODUCT_DB_NAMES = ("data/products.sqlite3", "products.sqlite3")
MANIFEST_NAME = "manifest.json"
MAX_PACKAGE_MEMBERS = 50_000
MAX_PACKAGE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedPackage:
    root: Path
    product_database: Path
    manifest: dict[str, object]


@dataclass(frozen=True, slots=True)
class MediaChange:
    target: Path
    backup_path: Path | None


class ProductPackageStore:
    def __init__(
        self,
        repository: SQLiteProductSyncRepository,
        *,
        drawing_dir: Path,
        image_dir: Path,
    ) -> None:
        self.repository = repository
        self.media_dirs = {
            "drawings": ("data/drawings", drawing_dir),
            "product_images": ("data/product_images", image_dir),
        }

    def export(self, output_path: Path, *, include_drawings: bool, include_images: bool) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        manifest: dict[str, object] = {
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
        try:
            with tempfile.TemporaryDirectory(prefix="bld-product-export-") as temporary_dir:
                temporary_root = Path(temporary_dir)
                product_database = temporary_root / "products.sqlite3"
                self.repository.export_products_database(product_database)
                with tarfile.open(temporary_output, "w:gz") as archive:
                    archive.add(product_database, arcname="data/products.sqlite3")
                    media_files = manifest["media_files"]
                    if not isinstance(media_files, dict):
                        raise RuntimeError("Product package manifest is invalid.")
                    if include_drawings:
                        media_files["drawings"] = self._add_directory(archive, "drawings")
                    if include_images:
                        media_files["product_images"] = self._add_directory(archive, "product_images")
                    manifest_path = temporary_root / MANIFEST_NAME
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                    archive.add(manifest_path, arcname=MANIFEST_NAME)
                os.replace(temporary_output, output_path)
        finally:
            temporary_output.unlink(missing_ok=True)
        return output_path

    @contextmanager
    def prepare(self, package_path: Path) -> Iterator[PreparedPackage]:
        with tempfile.TemporaryDirectory(prefix="bld-product-import-") as temporary_dir:
            root = Path(temporary_dir)
            self._safe_extract(package_path, root)
            product_database = self._find_product_database(root)
            self._validate_product_database(product_database)
            yield PreparedPackage(
                root=root,
                product_database=product_database,
                manifest=self._read_manifest(root),
            )

    def media_count(self, prepared: PreparedPackage, key: str) -> int:
        relative, _destination = self.media_dirs[key]
        source = prepared.root / relative
        return sum(1 for path in source.rglob("*") if path.is_file()) if source.exists() else 0

    def _add_directory(self, archive: tarfile.TarFile, key: str) -> int:
        arcname, source = self.media_dirs[key]
        if not source.exists():
            return 0
        source_root = source.resolve()
        count = 0
        for path in sorted(source.rglob("*")):
            resolved = path.resolve()
            if path.is_file() and not path.is_symlink() and source_root in resolved.parents:
                archive.add(path, arcname=str(Path(arcname) / path.relative_to(source)))
                count += 1
        return count

    @staticmethod
    def _safe_extract(
        package_path: Path,
        destination: Path,
        *,
        max_members: int = MAX_PACKAGE_MEMBERS,
        max_member_bytes: int = MAX_PACKAGE_MEMBER_BYTES,
        max_extracted_bytes: int = MAX_PACKAGE_EXTRACTED_BYTES,
    ) -> None:
        try:
            archive = tarfile.open(package_path, "r:gz")
        except (tarfile.TarError, OSError) as exc:
            raise ValueError("产品数据包无法读取。") from exc
        with archive:
            destination_resolved = destination.resolve()
            member_count = 0
            extracted_bytes = 0
            for member in archive:
                member_count += 1
                if member_count > max_members:
                    raise ValueError("数据包文件数量超过安全上限。")
                member_path = (destination / member.name).resolve()
                if destination_resolved != member_path and destination_resolved not in member_path.parents:
                    raise ValueError(f"数据包包含不安全路径：{member.name}")
                if member.issym() or member.islnk():
                    raise ValueError(f"数据包不能包含链接文件：{member.name}")
                if not member.isfile() and not member.isdir():
                    raise ValueError(f"数据包包含不支持的文件类型：{member.name}")
                if member.isfile():
                    if member.size < 0 or member.size > max_member_bytes:
                        raise ValueError(f"数据包单个文件超过安全上限：{member.name}")
                    extracted_bytes += member.size
                    if extracted_bytes > max_extracted_bytes:
                        raise ValueError("数据包解压总量超过安全上限。")
                archive.extract(member, destination, filter="data")

    @staticmethod
    def _find_product_database(root: Path) -> Path:
        for name in PRODUCT_DB_NAMES:
            path = root / name
            if path.is_file():
                return path
        raise ValueError("数据包里没有 products.sqlite3。")

    @staticmethod
    def _validate_product_database(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ValueError("products.sqlite3 完整性检查失败。")
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'products'"
            ).fetchone()
            if not exists:
                raise ValueError("products.sqlite3 缺少 products 表。")
        finally:
            connection.close()

    @staticmethod
    def _read_manifest(root: Path) -> dict[str, object]:
        path = root / MANIFEST_NAME
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


class ProductMediaSynchronizer:
    def __init__(self, media_dirs: dict[str, tuple[str, Path]]) -> None:
        self.media_dirs = media_dirs

    def copy(
        self,
        prepared: PreparedPackage,
        *,
        key: str,
        backup_dir: Path,
        changes: list[MediaChange],
    ) -> int:
        relative, destination = self.media_dirs[key]
        source = prepared.root / relative
        if not source.exists():
            return 0
        count = 0
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative_path = path.relative_to(source)
            target = destination / relative_path
            backup_path = None
            if target.exists():
                backup_path = backup_dir / relative / relative_path
                self.atomic_copy(target, backup_path)
            changes.append(MediaChange(target=target, backup_path=backup_path))
            self.atomic_copy(path, target)
            count += 1
        return count

    @classmethod
    def restore(cls, changes: list[MediaChange]) -> None:
        errors: list[str] = []
        for change in reversed(changes):
            try:
                if change.backup_path and change.backup_path.exists():
                    cls.atomic_copy(change.backup_path, change.target)
                else:
                    change.target.unlink(missing_ok=True)
            except OSError:
                errors.append(change.target.name)
        if errors:
            raise RuntimeError("媒体回滚失败，请检查备份目录。")

    @staticmethod
    def atomic_copy(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.sync-{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
