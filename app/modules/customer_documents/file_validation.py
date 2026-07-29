from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from .domain import CustomerDocumentValidationError


_OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")
_CONTENT_TYPES: dict[str, tuple[str, frozenset[str]]] = {
    ".pdf": ("application/pdf", frozenset({"application/pdf"})),
    ".png": ("image/png", frozenset({"image/png"})),
    ".jpg": ("image/jpeg", frozenset({"image/jpeg", "image/pjpeg"})),
    ".jpeg": ("image/jpeg", frozenset({"image/jpeg", "image/pjpeg"})),
    ".webp": ("image/webp", frozenset({"image/webp"})),
    ".doc": ("application/msword", frozenset({"application/msword"})),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
    ),
    ".xls": ("application/vnd.ms-excel", frozenset({"application/vnd.ms-excel"})),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}),
    ),
    ".xlsm": (
        "application/vnd.ms-excel.sheet.macroenabled.12",
        frozenset({"application/vnd.ms-excel.sheet.macroenabled.12"}),
    ),
    ".ppt": ("application/vnd.ms-powerpoint", frozenset({"application/vnd.ms-powerpoint"})),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation"}),
    ),
    ".csv": ("text/csv", frozenset({"text/csv", "text/plain", "application/vnd.ms-excel"})),
    ".txt": ("text/plain", frozenset({"text/plain"})),
}


def upload_parts(upload: object) -> tuple[str, str, BinaryIO]:
    filename = str(getattr(upload, "filename", "") or "")
    content_type = str(getattr(upload, "content_type", "") or getattr(upload, "mimetype", "") or "")
    stream = getattr(upload, "stream", upload)
    if not hasattr(stream, "read"):
        raise CustomerDocumentValidationError("customer_document.invalid_file", "上传文件无法读取。", field="files")
    return filename, content_type, cast(BinaryIO, stream)


def validate_upload_metadata(filename: str, declared_type: str) -> tuple[str, str, str]:
    name = PurePosixPath(filename.replace("\\", "/")).name.strip().strip(".")
    name = "".join(char for char in name if char.isprintable() and char not in "\r\n\0")
    if not name:
        raise CustomerDocumentValidationError(
            "customer_document.filename_required", "上传文件缺少文件名。", field="files"
        )
    if len(name) > 240:
        raise CustomerDocumentValidationError(
            "customer_document.filename_too_long", "上传文件名不能超过 240 个字符。", field="files"
        )
    suffix = Path(name).suffix.lower()
    if suffix not in _CONTENT_TYPES:
        allowed = "、".join(sorted(_CONTENT_TYPES))
        raise CustomerDocumentValidationError(
            "customer_document.extension_not_allowed",
            f"不支持 {suffix or '无扩展名'} 文件；允许：{allowed}。",
            field="files",
        )
    media_type, accepted = _CONTENT_TYPES[suffix]
    declared = declared_type.split(";", 1)[0].strip().lower()
    if declared and declared != "application/octet-stream" and declared not in accepted:
        raise CustomerDocumentValidationError(
            "customer_document.content_type_mismatch",
            f"文件 {suffix} 的内容类型与扩展名不一致。",
            field="files",
        )
    return name, suffix, media_type


def _valid_zip_container(path: Path, required_prefix: str) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    return "[Content_Types].xml" in names and any(name.startswith(required_prefix) for name in names)


def validate_file_signature(path: Path, suffix: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(16)
        sample = header + handle.read(65520) if suffix in {".csv", ".txt"} else b""
    valid = True
    if suffix == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif suffix == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif suffix == ".webp":
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    elif suffix in {".doc", ".xls", ".ppt"}:
        valid = header.startswith(_OLE_HEADER)
    elif suffix == ".docx":
        valid = _valid_zip_container(path, "word/")
    elif suffix in {".xlsx", ".xlsm"}:
        valid = _valid_zip_container(path, "xl/")
    elif suffix == ".pptx":
        valid = _valid_zip_container(path, "ppt/")
    elif suffix in {".csv", ".txt"}:
        valid = b"\0" not in sample
        if valid:
            try:
                sample.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    sample.decode("gb18030")
                except UnicodeDecodeError:
                    valid = False
    if not valid:
        raise CustomerDocumentValidationError(
            "customer_document.invalid_file_content",
            f"文件内容不是有效的 {suffix} 文件。",
            field="files",
        )
