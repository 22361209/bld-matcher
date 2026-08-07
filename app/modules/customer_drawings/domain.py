from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class CustomerDrawingDirection(StrEnum):
    CUSTOMER = "customer"
    ISSUED = "issued"


DIRECTION_LABELS: dict[str, str] = {
    CustomerDrawingDirection.CUSTOMER: "客户来图",
    CustomerDrawingDirection.ISSUED: "我方出图",
}

CUSTOMER_DRAWING_DIRECTIONS: tuple[dict[str, str], ...] = tuple(
    {"value": direction.value, "label": DIRECTION_LABELS[direction.value]} for direction in CustomerDrawingDirection
)

PREVIEWABLE_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

_BLD_NO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/ -]*")


class CustomerDrawingValidationError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclass(frozen=True, slots=True)
class CustomerIdentity:
    id: int
    name: str
    sync_id: str


@dataclass(frozen=True, slots=True)
class CustomerDrawingFile:
    id: int
    sync_id: str
    group_id: int
    version_no: int
    revision_label: str
    original_name: str
    storage_path: str = field(repr=False)
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    sha256: str = ""
    uploaded_by: str = ""
    note: str = ""
    created_at: str = ""

    @property
    def previewable(self) -> bool:
        return self.content_type in PREVIEWABLE_CONTENT_TYPES

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sync_id": self.sync_id,
            "group_id": self.group_id,
            "version_no": self.version_no,
            "revision_label": self.revision_label,
            "original_name": self.original_name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "uploaded_by": self.uploaded_by,
            "note": self.note,
            "created_at": self.created_at,
            "previewable": self.previewable,
        }


@dataclass(frozen=True, slots=True)
class CustomerDrawingVersion:
    version_no: int
    revision_label: str
    note: str
    file: CustomerDrawingFile


@dataclass(frozen=True, slots=True)
class CustomerDrawingGroup:
    id: int
    customer_id: int
    sync_id: str
    direction: str
    bld_no: str
    title: str
    drawing_no: str
    current_version: int
    archived: bool
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    files: tuple[CustomerDrawingFile, ...] = ()

    @property
    def direction_label(self) -> str:
        return DIRECTION_LABELS.get(self.direction, DIRECTION_LABELS[CustomerDrawingDirection.CUSTOMER])

    @property
    def current_file(self) -> CustomerDrawingFile | None:
        for item in self.files:
            if item.version_no == self.current_version:
                return item
        return None

    @property
    def versions(self) -> tuple[CustomerDrawingVersion, ...]:
        return tuple(
            CustomerDrawingVersion(
                version_no=item.version_no,
                revision_label=item.revision_label,
                note=item.note,
                file=item,
            )
            for item in sorted(self.files, key=lambda entry: entry.version_no, reverse=True)
        )

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "sync_id": self.sync_id,
            "direction": self.direction,
            "direction_label": self.direction_label,
            "bld_no": self.bld_no,
            "title": self.title,
            "drawing_no": self.drawing_no,
            "current_version": self.current_version,
            "archived": self.archived,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files": [item.payload() for item in self.files],
            "current_file": self.current_file.payload() if self.current_file else None,
            "versions": [
                {
                    "version_no": version.version_no,
                    "revision_label": version.revision_label,
                    "note": version.note,
                    "file": version.file.payload(),
                }
                for version in self.versions
            ],
        }


@dataclass(frozen=True, slots=True)
class CustomerDrawingFileReference:
    file: CustomerDrawingFile
    group_id: int
    customer_id: int
    direction: str
    title: str
    current_version: int
    group_archived: bool

    @property
    def direction_label(self) -> str:
        return DIRECTION_LABELS.get(self.direction, DIRECTION_LABELS[CustomerDrawingDirection.CUSTOMER])


@dataclass(frozen=True, slots=True)
class CustomerDrawingSummary:
    group_count: int = 0
    file_count: int = 0

    def payload(self) -> dict[str, int]:
        return {
            "group_count": self.group_count,
            "file_count": self.file_count,
        }


def clean_direction(value: object) -> str:
    direction = str(value or "").strip().lower()
    if direction not in DIRECTION_LABELS:
        raise CustomerDrawingValidationError(
            "customer_drawing.invalid_direction", "请选择有效的图纸方向。", field="direction"
        )
    return direction


def clean_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise CustomerDrawingValidationError("customer_drawing.title_required", "图纸标题不能为空。", field="title")
    if len(title) > 120:
        raise CustomerDrawingValidationError(
            "customer_drawing.title_too_long", "图纸标题不能超过 120 个字符。", field="title"
        )
    return title


def clean_bld_no(value: object) -> str:
    bld_no = " ".join(str(value or "").split()).upper()
    if len(bld_no) > 80:
        raise CustomerDrawingValidationError(
            "customer_drawing.bld_no_too_long", "关联 BLD 号不能超过 80 个字符。", field="bld_no"
        )
    if bld_no and not _BLD_NO_PATTERN.fullmatch(bld_no):
        raise CustomerDrawingValidationError(
            "customer_drawing.invalid_bld_no", "关联 BLD 号只能包含字母、数字和常见分隔符。", field="bld_no"
        )
    return bld_no


def clean_drawing_no(value: object) -> str:
    drawing_no = " ".join(str(value or "").split())
    if len(drawing_no) > 120:
        raise CustomerDrawingValidationError(
            "customer_drawing.drawing_no_too_long", "图号不能超过 120 个字符。", field="drawing_no"
        )
    return drawing_no


def clean_revision_label(value: object) -> str:
    revision_label = " ".join(str(value or "").split())
    if len(revision_label) > 60:
        raise CustomerDrawingValidationError(
            "customer_drawing.revision_label_too_long", "版本代号不能超过 60 个字符。", field="revision_label"
        )
    return revision_label


def clean_note(value: object) -> str:
    note = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(note) > 2000:
        raise CustomerDrawingValidationError(
            "customer_drawing.note_too_long", "版本备注不能超过 2000 个字符。", field="note"
        )
    return note
