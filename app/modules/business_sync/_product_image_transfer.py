from __future__ import annotations

import hashlib
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypedDict

from app.config import PRODUCT_IMAGE_DATA_PREFIX
from app.platform.clock import now_text
from app.product_image_processing import (
    PRODUCT_IMAGE_OUTPUT_SUFFIX,
    atomic_write_bytes,
    process_synced_product_image,
    validate_synced_product_image,
)
from app.product_media import (
    IMAGE_SLOT_FIELDS,
    image_slot_field,
    product_image_storage_candidates,
    product_image_storage_name,
)

from ._media_transaction import atomic_copy, normalized_media_target, safe_media_target


PRODUCT_IMAGE_SLOTS_FIELD = "product_image_slots"
PRODUCT_IMAGE_MEMBER_PREFIX = "data/product_images/"


class ProductImageEntry(TypedDict):
    bld_no: str
    slot: int
    file: str
    sha256: str


MediaChange = tuple[Path, Path | None]


@dataclass(frozen=True, slots=True)
class ProductImageImportPlan:
    bld_no: str
    slot: int
    file_name: str
    product_id: int
    field: str
    old_name: str | None
    destination_name: str
    destination: Path
    destination_thumb: Path


def _direct_name(value: object) -> str | None:
    name = str(value or "").strip()
    if not name or "\\" in name:
        return None
    path = PurePosixPath(name)
    return name if not path.is_absolute() and len(path.parts) == 1 and path.name == name else None


def _source_for_slot(image_dir: Path, row: dict[str, object], field: str, slot: int) -> Path | None:
    reference = str(row.get(field) or "")
    name = None
    if reference.startswith(PRODUCT_IMAGE_DATA_PREFIX):
        name = _direct_name(reference[len(PRODUCT_IMAGE_DATA_PREFIX) :])
        if name is None:
            raise ValueError("产品图片引用路径无效，不能导出业务数据包。")
    elif not reference:
        for candidate in product_image_storage_candidates(row.get("bld_no"), PRODUCT_IMAGE_OUTPUT_SUFFIX, slot):
            if (image_dir / candidate).is_file():
                return image_dir / candidate
    if name is None:
        return None
    source = image_dir / name
    if source.is_file():
        return source
    if reference.startswith(PRODUCT_IMAGE_DATA_PREFIX):
        raise ValueError(f"产品图片 {name} 缺失，不能导出业务数据包。")
    return None


def collect_product_image_exports(
    product_rows: list[dict[str, object]],
    image_dir: Path | None,
) -> tuple[list[ProductImageEntry], dict[str, Path]]:
    has_references = any(
        str(row.get(field) or "").startswith(PRODUCT_IMAGE_DATA_PREFIX)
        for row in product_rows
        for field in IMAGE_SLOT_FIELDS
    )
    if image_dir is None:
        if has_references:
            raise ValueError("当前系统未配置产品图片目录，不能导出业务数据包。")
        return [], {}
    if not image_dir.is_dir():
        if has_references:
            raise ValueError("产品图片目录缺失，不能导出业务数据包。")
        return [], {}
    root = image_dir.resolve()
    entries: list[ProductImageEntry] = []
    sources: dict[str, Path] = {}
    portable_sources: dict[str, str] = {}
    portable_destinations: dict[str, tuple[str, int]] = {}
    for row in product_rows:
        bld_no = str(row.get("bld_no") or "").strip()
        if not bld_no:
            continue
        for slot, field in enumerate(IMAGE_SLOT_FIELDS, start=1):
            source = _source_for_slot(image_dir, row, field, slot)
            if source is None:
                continue
            try:
                resolved = source.resolve(strict=True)
                stat = source.stat()
            except OSError as exc:
                raise ValueError(f"产品图片 {source.name} 无法读取，不能导出业务数据包。") from exc
            if source.is_symlink() or stat.st_nlink > 1 or resolved.parent != root:
                raise ValueError(f"产品图片 {source.name} 不是可安全导出的普通文件。")
            payload = source.read_bytes()
            validate_synced_product_image(payload)
            file_name = source.name
            portable_file = normalized_media_target(Path(file_name))
            existing_file = portable_sources.get(portable_file)
            if existing_file is not None and existing_file != file_name:
                raise ValueError("产品图片包含会指向同一跨平台目标的文件名。")
            portable_sources[portable_file] = file_name
            destination_name = product_image_storage_name(bld_no, PRODUCT_IMAGE_OUTPUT_SUFFIX, slot)
            portable_destination = normalized_media_target(Path(destination_name))
            destination_identity = (bld_no, slot)
            existing_destination = portable_destinations.get(portable_destination)
            if existing_destination is not None and existing_destination != destination_identity:
                raise ValueError("产品型号生成了重复的跨平台图片目标，请先整理 BLD 型号。")
            portable_destinations[portable_destination] = destination_identity
            digest = hashlib.sha256(payload).hexdigest()
            sources.setdefault(file_name, source)
            entries.append({"bld_no": bld_no, "slot": slot, "file": file_name, "sha256": digest})
    entries.sort(key=lambda entry: (str(entry["bld_no"]), int(entry["slot"])))
    return entries, sources


def add_product_images(archive: tarfile.TarFile, sources: dict[str, Path]) -> int:
    for file_name, source in sorted(sources.items()):
        archive.add(source, arcname=f"{PRODUCT_IMAGE_MEMBER_PREFIX}{file_name}")
    return len(sources)


def validate_product_image_manifest(
    version: int,
    media: dict[str, object],
    payload: dict[str, list[dict[str, object]]],
    product_members: dict[str, tuple[int, str]],
) -> None:
    raw_entries = media.get(PRODUCT_IMAGE_SLOTS_FIELD)
    if version < 4:
        if PRODUCT_IMAGE_SLOTS_FIELD in media:
            raise ValueError("旧版业务数据包不能声明新版产品图片映射。")
        return
    if not isinstance(raw_entries, list):
        raise ValueError("业务数据包缺少产品图片映射。")
    product_bld_numbers = {
        str(row.get("bld_no") or "") for row in payload.get("products", []) if row.get("active") not in (0, False, "0")
    }
    seen_slots: set[tuple[str, int]] = set()
    referenced_files: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"bld_no", "slot", "file", "sha256"}:
            raise ValueError("业务数据包产品图片映射无效。")
        bld_no = raw_entry.get("bld_no")
        slot = raw_entry.get("slot")
        file_name = _direct_name(raw_entry.get("file"))
        digest = raw_entry.get("sha256")
        if (
            not isinstance(bld_no, str)
            or not bld_no.strip()
            or bld_no not in product_bld_numbers
            or type(slot) is not int
            or not 1 <= slot <= len(IMAGE_SLOT_FIELDS)
            or file_name is None
            or Path(file_name).suffix.lower() != PRODUCT_IMAGE_OUTPUT_SUFFIX
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("业务数据包产品图片映射无效。")
        identity = (bld_no, slot)
        if identity in seen_slots:
            raise ValueError("业务数据包包含重复的产品图片位置。")
        seen_slots.add(identity)
        referenced_files.add(file_name)
        member = product_members.get(file_name)
        if member is None or member[1] != digest:
            raise ValueError("业务数据包产品图片与映射校验值不一致。")
    if referenced_files != set(product_members):
        raise ValueError("业务数据包产品图片与映射不完整。")


def product_image_entries(manifest: dict[str, object]) -> list[ProductImageEntry]:
    media = manifest.get("media", {})
    if not isinstance(media, dict):
        return []
    entries = media.get(PRODUCT_IMAGE_SLOTS_FIELD, [])
    if not isinstance(entries, list):
        return []
    return [
        {
            "bld_no": str(entry["bld_no"]),
            "slot": int(entry["slot"]),
            "file": str(entry["file"]),
            "sha256": str(entry["sha256"]),
        }
        for entry in entries
        if isinstance(entry, dict)
    ]


def _backup_target(
    target: Path,
    relative: Path,
    backup_root: Path,
    changes: list[MediaChange],
    *,
    remove: bool = False,
) -> None:
    backup = safe_media_target(backup_root / "product_images_v4", relative) if target.exists() else None
    if backup is not None:
        atomic_copy(target, backup)
    changes.append((target, backup))
    if remove:
        target.unlink(missing_ok=True)


def _existing_image_name(row: sqlite3.Row, field: str, slot: int, image_dir: Path) -> str | None:
    reference = str(row[field] or "")
    if reference.startswith(PRODUCT_IMAGE_DATA_PREFIX):
        return _direct_name(reference[len(PRODUCT_IMAGE_DATA_PREFIX) :])
    if reference:
        return None
    for suffix in (PRODUCT_IMAGE_OUTPUT_SUFFIX, ".jpg", ".jpeg", ".png"):
        for candidate in product_image_storage_candidates(row["bld_no"], suffix, slot):
            if (image_dir / candidate).is_file():
                return candidate
    return None


def _referenced_image_names(connection: sqlite3.Connection, image_dir: Path) -> set[str]:
    fields = ", ".join(IMAGE_SLOT_FIELDS)
    rows = connection.execute(f"SELECT bld_no, {fields} FROM products").fetchall()
    references: set[str] = set()
    for row in rows:
        for slot, field in enumerate(IMAGE_SLOT_FIELDS, start=1):
            name = _existing_image_name(row, field, slot, image_dir)
            if name:
                references.add(name)
    return references


def apply_product_images(
    connection: sqlite3.Connection,
    package_path: Path,
    manifest: dict[str, object],
    image_dir: Path,
    backup_root: Path,
    changes: list[MediaChange],
) -> int:
    entries = product_image_entries(manifest)
    image_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir = image_dir / "thumbs"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    target_names: dict[str, tuple[str, int]] = {}
    plans_by_file: dict[str, list[ProductImageImportPlan]] = {}
    source_digests = {entry["file"]: entry["sha256"] for entry in entries}
    for entry in entries:
        bld_no = entry["bld_no"]
        slot = entry["slot"]
        destination_name = product_image_storage_name(bld_no, PRODUCT_IMAGE_OUTPUT_SUFFIX, slot)
        portable_name = normalized_media_target(Path(destination_name))
        identity = (bld_no, slot)
        existing = target_names.get(portable_name)
        if existing is not None and existing != identity:
            raise ValueError("业务数据包产品图片会写入重复的跨平台目标。")
        target_names[portable_name] = identity
        row = connection.execute("SELECT * FROM products WHERE bld_no = ?", (bld_no,)).fetchone()
        if row is None:
            raise ValueError(f"业务数据包产品图片找不到本机型号 {bld_no}。")
        field = image_slot_field(slot)
        old_name = _existing_image_name(row, field, slot, image_dir)
        destination = safe_media_target(image_dir, Path(destination_name))
        destination_thumb = safe_media_target(thumb_dir, Path(destination_name))
        plan = ProductImageImportPlan(
            bld_no=bld_no,
            slot=slot,
            file_name=entry["file"],
            product_id=int(row["id"]),
            field=field,
            old_name=old_name,
            destination_name=destination_name,
            destination=destination,
            destination_thumb=destination_thumb,
        )
        plans_by_file.setdefault(plan.file_name, []).append(plan)

    changed_targets: set[Path] = set()
    old_names = {plan.old_name for plans in plans_by_file.values() for plan in plans if plan.old_name}
    with tarfile.open(package_path, "r:gz") as archive:
        for file_name, plans in sorted(plans_by_file.items()):
            source = archive.extractfile(f"{PRODUCT_IMAGE_MEMBER_PREFIX}{file_name}")
            if source is None:
                raise ValueError("业务数据包产品图片无法读取。")
            try:
                payload = source.read()
            finally:
                source.close()
            if hashlib.sha256(payload).hexdigest() != source_digests[file_name]:
                raise ValueError("业务数据包产品图片与映射校验值不一致。")
            image = process_synced_product_image(payload)
            for plan in plans:
                _backup_target(plan.destination, Path(plan.destination_name), backup_root, changes)
                atomic_write_bytes(plan.destination, image.large)
                changed_targets.add(plan.destination)
                _backup_target(
                    plan.destination_thumb,
                    Path("thumbs", plan.destination_name),
                    backup_root,
                    changes,
                )
                atomic_write_bytes(plan.destination_thumb, image.thumbnail)
                changed_targets.add(plan.destination_thumb)
                connection.execute(
                    f"UPDATE products SET {plan.field} = ?, updated_at = ? WHERE id = ?",
                    (
                        f"{PRODUCT_IMAGE_DATA_PREFIX}{plan.destination_name}",
                        now_text(),
                        plan.product_id,
                    ),
                )

    final_references = _referenced_image_names(connection, image_dir)
    for old_name in sorted(old_names - final_references):
        old_image = safe_media_target(image_dir, Path(old_name))
        old_thumb_name = f"{Path(old_name).stem}{PRODUCT_IMAGE_OUTPUT_SUFFIX}"
        old_thumb = safe_media_target(thumb_dir, Path(old_thumb_name))
        if old_image not in changed_targets and old_image.exists():
            _backup_target(old_image, Path(old_name), backup_root, changes, remove=True)
        if old_thumb not in changed_targets and old_thumb.exists():
            _backup_target(old_thumb, Path("thumbs", old_thumb_name), backup_root, changes, remove=True)
    return len(entries)
