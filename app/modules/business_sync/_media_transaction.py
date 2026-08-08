from __future__ import annotations

import os
import shutil
import tarfile
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO

from ._schema import MEDIA_DIRECTORIES


PathCopy = Callable[[Path, Path], None]
StreamCopy = Callable[[IO[bytes], Path], None]


def media_relative_path(member_name: str, key: str) -> Path:
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


def normalized_media_target(relative: Path) -> str:
    return unicodedata.normalize("NFC", relative.as_posix()).casefold()


def existing_media_targets(destination: Path, key: str) -> dict[str, Path]:
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
            normalized_target = normalized_media_target(relative)
            existing = targets.get(normalized_target)
            if existing is not None and existing != relative:
                raise ValueError("当前系统媒体目录包含仅大小写或 Unicode 形式不同的重复文件，请先整理后再导入。")
            targets[normalized_target] = relative
        return targets
    except ValueError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ValueError("业务数据媒体目标目录无效。") from exc


def safe_media_target(destination: Path, relative: Path) -> Path:
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


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_copy_stream(source: IO[bytes], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as output:
            shutil.copyfileobj(source, output)
        os.replace(temporary, target)
    finally:
        source.close()
        temporary.unlink(missing_ok=True)


def _copy_media_members(
    archive: tarfile.TarFile,
    members: Iterable[tarfile.TarInfo],
    key: str,
    destination: Path,
    existing_targets: dict[str, Path],
    backup_root: Path,
    changes: list[tuple[Path, Path | None]],
    *,
    atomic_copy_fn: PathCopy = atomic_copy,
    atomic_copy_stream_fn: StreamCopy = atomic_copy_stream,
) -> None:
    prefix = f"{MEDIA_DIRECTORIES[key]}/"
    for member in members:
        if not member.isfile() or not member.name.startswith(prefix):
            continue
        relative = media_relative_path(member.name, key)
        normalized_target = normalized_media_target(relative)
        target_relative = existing_targets.get(normalized_target, relative)
        target = safe_media_target(destination, target_relative)
        backup = safe_media_target(backup_root / key, target_relative) if target.exists() else None
        if backup is not None:
            atomic_copy_fn(target, backup)
        changes.append((target, backup))
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("业务数据包媒体文件无法读取。")
        try:
            atomic_copy_stream_fn(source, target)
        finally:
            source.close()
        existing_targets[normalized_target] = target_relative


def copy_requested_media(
    package_path: Path,
    manifest: dict[str, object],
    requests: dict[str, bool],
    backup_root: Path,
    changes: list[tuple[Path, Path | None]],
    *,
    media_dirs: dict[str, Path | None],
    media_summary: Callable[[dict[str, object]], dict[str, object]],
    atomic_copy_fn: PathCopy = atomic_copy,
    atomic_copy_stream_fn: StreamCopy = atomic_copy_stream,
) -> None:
    summary = media_summary(manifest)
    selected = [key for key, requested in requests.items() if requested and summary.get(key)]
    if not selected:
        return

    destinations: dict[str, Path] = {}
    for key in selected:
        destination = media_dirs[key]
        if destination is None:
            raise ValueError("当前系统未配置业务数据媒体目录。")
        destinations[key] = destination

    with tarfile.open(package_path, "r:gz") as archive:
        members = archive.getmembers()
        for key in selected:
            existing_targets = existing_media_targets(destinations[key], key)
            _copy_media_members(
                archive,
                members,
                key,
                destinations[key],
                existing_targets,
                backup_root,
                changes,
                atomic_copy_fn=atomic_copy_fn,
                atomic_copy_stream_fn=atomic_copy_stream_fn,
            )


def restore_media(
    changes: list[tuple[Path, Path | None]],
    *,
    atomic_copy_fn: PathCopy = atomic_copy,
) -> None:
    for target, backup in reversed(changes):
        if backup is not None and backup.exists():
            atomic_copy_fn(backup, target)
        else:
            target.unlink(missing_ok=True)
