"""Immutable development revision transport; deliberately separate from legacy media."""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from types import SimpleNamespace
import tarfile
import uuid
from pathlib import Path


from app.modules.development.file_store import ALLOWED_EXTENSIONS, MAX_FILE_BYTES, DevelopmentFileStore
from app.modules.development.file_validation import validate_signature


logger = logging.getLogger(__name__)


MEMBER_PREFIX = "development/revisions/"
MAX_REVISION_BYTES = MAX_FILE_BYTES


def revision_path(row: dict) -> str:
    try:
        project_id = str(uuid.UUID(str(row["project_id"])))
        revision_id = str(uuid.UUID(str(row["id"])))
        digest = row["sha256"]
        extension = str(row["extension"]).removeprefix(".").lower()
        size = row["size_bytes"]
        if project_id != row["project_id"] or revision_id != row["id"]:
            raise ValueError
        if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError
        if "." + extension not in ALLOWED_EXTENSIONS or type(size) is not int or not 0 < size <= MAX_REVISION_BYTES:
            raise ValueError
        relative = f"{project_id}/{revision_id}/{digest}.{extension}"
        if row["storage_path"] != relative:
            raise ValueError
        return relative
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("开发修订文件身份、类型、路径或大小无效。") from exc


def safe_target(root: Path | None, relative: str) -> Path:
    if root is None:
        raise ValueError("当前系统未配置开发文件目录。")
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {".", ".."} for part in parts):
        raise ValueError("开发文件路径无效。")
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("开发文件路径不能使用符号链接。")
    if root.is_symlink() or (candidate.exists() and (not candidate.is_file() or candidate.stat().st_nlink != 1)):
        raise ValueError("开发文件目标不能使用链接或特殊文件。")
    resolved_root = root.resolve()
    if resolved_root not in candidate.resolve().parents:
        raise ValueError("开发文件路径越界。")
    return candidate


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def revision_rows(aggregates: list[dict]) -> list[dict]:
    return [row for aggregate in aggregates for row in aggregate.get("revisions", [])]


def file_snapshot(root: Path | None, aggregates: list[dict]) -> dict[str, str]:
    result = {}
    for row in revision_rows(aggregates):
        relative = revision_path(row)
        path = safe_target(root, relative)
        if path.parent.exists() and any(sibling != path for sibling in path.parent.iterdir()):
            raise ValueError("同一开发修订 ID 对应不同文件内容，拒绝覆盖。")
        result[relative] = digest_file(path) if path.exists() else "missing"
    return result


def validate_archive_files(archive: tarfile.TarFile, aggregates: list[dict]) -> None:
    expected = {}
    for row in revision_rows(aggregates):
        relative = revision_path(row)
        if relative in expected:
            raise ValueError("开发数据包包含重复修订文件。")
        expected[relative] = row
    actual = {member.name[len(MEMBER_PREFIX):]: member for member in archive.getmembers() if member.name.startswith(MEMBER_PREFIX)}
    if set(actual) != set(expected):
        raise ValueError("开发数据包修订文件缺失或包含未引用文件。")
    for relative, row in expected.items():
        member = actual[relative]
        source = archive.extractfile(member)
        if source is None or member.size != row["size_bytes"]:
            raise ValueError("开发修订文件大小不一致。")
        with source, tempfile.NamedTemporaryFile(suffix="." + str(row["extension"]).lstrip(".")) as temporary:
            while chunk := source.read(1024 * 1024):
                temporary.write(chunk)
            temporary.flush()
            path = Path(temporary.name)
            if digest_file(path) != row["sha256"]:
                raise ValueError("开发修订文件 SHA256 校验失败。")
            validate_signature(path, "." + str(row["extension"]).lstrip("."))


def add_archive_files(archive: tarfile.TarFile, root: Path | None, aggregates: list[dict]) -> None:
    for row in revision_rows(aggregates):
        relative = revision_path(row)
        source = safe_target(root, relative)
        if not source.is_file() or source.stat().st_size != row["size_bytes"] or digest_file(source) != row["sha256"]:
            raise ValueError("本机开发修订文件缺失或 SHA256 校验失败。")
        archive.add(source, arcname=MEMBER_PREFIX + relative, recursive=False)


def copy_accepted_files(package_path: Path, root: Path | None, aggregates: list[dict], changes: list) -> None:
    if not revision_rows(aggregates):
        return
    if root is None:
        raise ValueError("当前系统未配置开发文件目录。")
    store = DevelopmentFileStore(root)
    with tarfile.open(package_path, "r:gz") as archive:
        for row in revision_rows(aggregates):
            relative = revision_path(row)
            target = safe_target(root, relative)
            if target.exists():
                if digest_file(target) != row["sha256"] or target.stat().st_size != row["size_bytes"]:
                    raise ValueError("同一开发修订 ID 对应不同文件内容，拒绝覆盖。")
                continue
            source = archive.extractfile(MEMBER_PREFIX + relative)
            if source is None:
                raise ValueError("开发修订文件无法读取。")
            with source:
                prepared = store.prepare(SimpleNamespace(filename=row["original_name"], content_type=row["content_type"], stream=source), project_id=row["project_id"], revision_id=row["id"], expected_sha256=row["sha256"])
            changes.append((store, prepared))
            store.promote(prepared)


def restore_development_files(changes: list) -> None:
    failures = []
    for store, prepared in reversed(changes):
        try:
            store.compensate(prepared)
        except Exception as exc:
            logger.exception("Development sync file compensation failed", extra={"revision_id": prepared.revision_id})
            failures.append(exc)
    if failures:
        raise ValueError("开发文件回滚发生冲突，已尝试恢复其他文件，请联系管理员。") from failures[0]
