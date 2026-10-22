from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from .document_domain import DevelopmentDocumentError


CONTENT_TYPES = {
    ".pdf": "application/pdf", ".dwg": "application/acad", ".dxf": "application/dxf",
    ".step": "model/step", ".stp": "model/step", ".iges": "model/iges", ".igs": "model/iges",
    ".sldprt": "application/octet-stream", ".sldasm": "application/octet-stream",
    ".slddrw": "application/octet-stream",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain", ".csv": "text/csv",
}
DRAWING_EXTENSIONS = frozenset({".pdf", ".dwg", ".dxf", ".step", ".stp", ".iges", ".igs",
                                ".sldprt", ".sldasm", ".slddrw"})


def upload_metadata(filename: str, declared_type: str) -> tuple[str, str, str]:
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name or len(name) > 240 or any(ord(char) < 32 for char in name):
        raise DevelopmentDocumentError("development.file_name", "文件名无效或超过 240 字。")
    suffix = Path(name).suffix.lower()
    if suffix not in CONTENT_TYPES:
        raise DevelopmentDocumentError("development.file_extension", "仅支持 PDF、白名单 CAD 源文件及 DOCX/XLSX/TXT/CSV 工艺附件。")
    declared = declared_type.partition(";")[0].strip().lower()
    accepted = {"", "application/octet-stream", CONTENT_TYPES[suffix]}
    if suffix in {".dwg", ".dxf"}:
        accepted.update({"image/vnd.dwg", "image/vnd.dxf", "application/x-autocad", "application/x-dxf"})
    if suffix in {".step", ".stp", ".iges", ".igs", ".dxf", ".csv"}:
        accepted.add("text/plain")
    if declared not in accepted:
        raise DevelopmentDocumentError("development.file_type", "文件类型与扩展名不一致。")
    return name, suffix, CONTENT_TYPES[suffix]


def validate_signature(path: Path, suffix: str) -> None:
    with path.open("rb") as handle:
        sample = handle.read(65536)
    valid = False
    if suffix == ".pdf":
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 2048))
            trailer = handle.read()
        valid = sample.startswith(b"%PDF-") and b"%%EOF" in trailer
    elif suffix == ".dwg":
        valid = bool(re.match(rb"AC10\d{2}", sample))
    elif suffix == ".dxf":
        valid = sample.startswith(b"AutoCAD Binary DXF\r\n\x1a\0") or bool(
            re.search(rb"(?:^|\r?\n)\s*0\r?\n\s*SECTION(?:\r?\n)", sample)
        )
    elif suffix in {".step", ".stp"}:
        valid = sample.lstrip().startswith(b"ISO-10303-21;")
    elif suffix in {".iges", ".igs"}:
        valid = any(len(line) >= 73 and line[72:73] == b"S" for line in sample.splitlines()[:10])
    elif suffix in {".sldprt", ".sldasm", ".slddrw"}:
        valid = sample.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    elif suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                names = {member.filename for member in members}
                prefix = "word/" if suffix == ".docx" else "xl/"
                valid = (
                    len(members) <= 10000
                    and sum(member.file_size for member in members) <= 200 * 1024 * 1024
                    and "[Content_Types].xml" in names
                    and any(name.startswith(prefix) for name in names)
                    and not any("vbaproject" in name.lower() for name in names)
                )
        except (OSError, zipfile.BadZipFile):
            valid = False
    elif suffix in {".txt", ".csv"}:
        try:
            sample.decode("utf-8-sig")
            valid = b"\0" not in sample and not sample.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<script"))
        except UnicodeDecodeError:
            valid = False
    if not valid:
        raise DevelopmentDocumentError("development.file_content", "文件内容与所选格式不符，或文件不完整。")
