from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from .domain import CustomerProductValidationError


_CONTENT_TYPES: dict[str, tuple[str, frozenset[str]]] = {
    ".pdf": ("application/pdf", frozenset({"application/pdf"})),
    ".png": ("image/png", frozenset({"image/png"})),
    ".jpg": ("image/jpeg", frozenset({"image/jpeg", "image/pjpeg"})),
    ".jpeg": ("image/jpeg", frozenset({"image/jpeg", "image/pjpeg"})),
    ".webp": ("image/webp", frozenset({"image/webp"})),
}


def upload_parts(upload: object) -> tuple[str, str, BinaryIO]:
    filename = str(getattr(upload, "filename", "") or "")
    content_type = str(getattr(upload, "content_type", "") or getattr(upload, "mimetype", "") or "")
    stream = getattr(upload, "stream", upload)
    if not hasattr(stream, "read"):
        raise CustomerProductValidationError("customer_drawing.invalid_file", "上传文件无法读取。", field="files")
    return filename, content_type, cast(BinaryIO, stream)


def validate_upload_metadata(filename: str, declared_type: str) -> tuple[str, str, str]:
    name = PurePosixPath(filename.replace("\\", "/")).name.strip().strip(".")
    name = "".join(char for char in name if char.isprintable() and char not in "\r\n\0")
    if not name:
        raise CustomerProductValidationError(
            "customer_drawing.filename_required", "上传文件缺少文件名。", field="files"
        )
    if len(name) > 240:
        raise CustomerProductValidationError(
            "customer_drawing.filename_too_long", "上传文件名不能超过 240 个字符。", field="files"
        )
    suffix = Path(name).suffix.lower()
    if suffix not in _CONTENT_TYPES:
        allowed = "、".join(sorted(_CONTENT_TYPES))
        raise CustomerProductValidationError(
            "customer_drawing.extension_not_allowed",
            f"不支持 {suffix or '无扩展名'} 文件；图纸仅允许：{allowed}。",
            field="files",
        )
    media_type, accepted = _CONTENT_TYPES[suffix]
    declared = declared_type.split(";", 1)[0].strip().lower()
    if declared and declared != "application/octet-stream" and declared not in accepted:
        raise CustomerProductValidationError(
            "customer_drawing.content_type_mismatch",
            f"文件 {suffix} 的内容类型与扩展名不一致。",
            field="files",
        )
    return name, suffix, media_type


def validate_file_signature(path: Path, suffix: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(16)
    valid = True
    if suffix == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif suffix == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif suffix == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if not valid:
        raise CustomerProductValidationError(
            "customer_drawing.invalid_file_content",
            f"文件内容不是有效的 {suffix} 文件。",
            field="files",
        )
