from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class CustomerDrawingKind(StrEnum):
    BLD = "bld"
    CUSTOMER = "customer"


KIND_LABELS: dict[str, str] = {
    CustomerDrawingKind.BLD: "BLD 图纸",
    CustomerDrawingKind.CUSTOMER: "客户图纸",
}

CUSTOMER_DRAWING_KINDS: tuple[dict[str, str], ...] = tuple(
    {"value": kind.value, "label": KIND_LABELS[kind.value]} for kind in CustomerDrawingKind
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


class CustomerProductValidationError(ValueError):
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
class QuotedProductOption:
    bld_no: str
    customer_product_code: str = ""

    def payload(self) -> dict[str, str]:
        return {
            "bld_no": self.bld_no,
            "customer_product_code": self.customer_product_code,
        }


@dataclass(frozen=True, slots=True)
class CatalogProductInfo:
    bld_no: str
    item_name: str = ""
    image_url: str = ""
    thumb_url: str = ""
    has_drawing: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "bld_no": self.bld_no,
            "item_name": self.item_name,
            "image_url": self.image_url,
            "thumb_url": self.thumb_url,
            "has_drawing": self.has_drawing,
        }


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
class CustomerDrawingSlot:
    """客户商品行下的一个图纸位（BLD 图纸 / 客户图纸），携带全部版本文件。"""

    id: int
    customer_product_id: int
    customer_id: int
    sync_id: str
    kind: str
    current_version: int
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    files: tuple[CustomerDrawingFile, ...] = ()

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, KIND_LABELS[CustomerDrawingKind.CUSTOMER])

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
            "customer_product_id": self.customer_product_id,
            "customer_id": self.customer_id,
            "sync_id": self.sync_id,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "current_version": self.current_version,
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
class CustomerProduct:
    id: int
    customer_id: int
    sync_id: str
    bld_no: str
    customer_product_code: str
    customer_product_name: str
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    drawings: tuple[CustomerDrawingSlot, ...] = ()
    catalog: CatalogProductInfo | None = None

    def slot(self, kind: str) -> CustomerDrawingSlot | None:
        for drawing in self.drawings:
            if drawing.kind == kind:
                return drawing
        return None

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "sync_id": self.sync_id,
            "bld_no": self.bld_no,
            "customer_product_code": self.customer_product_code,
            "customer_product_name": self.customer_product_name,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "drawings": [drawing.payload() for drawing in self.drawings],
            "catalog": self.catalog.payload() if self.catalog else None,
        }


@dataclass(frozen=True, slots=True)
class CustomerDrawingFileReference:
    file: CustomerDrawingFile
    group_id: int
    customer_id: int
    customer_product_id: int
    kind: str
    bld_no: str
    customer_product_name: str
    current_version: int

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, KIND_LABELS[CustomerDrawingKind.CUSTOMER])

    @property
    def title(self) -> str:
        parts = [part for part in (self.bld_no, self.customer_product_name) if part]
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class CustomerDrawingSummary:
    group_count: int = 0
    file_count: int = 0

    def payload(self) -> dict[str, int]:
        return {
            "group_count": self.group_count,
            "file_count": self.file_count,
        }


def clean_kind(value: object) -> str:
    kind = str(value or "").strip().lower()
    if kind not in KIND_LABELS:
        raise CustomerProductValidationError(
            "customer_drawing.invalid_kind", "请选择有效的图纸位。", field="kind"
        )
    return kind


def clean_bld_no(value: object) -> str:
    bld_no = " ".join(str(value or "").split()).upper()
    if not bld_no:
        raise CustomerProductValidationError(
            "customer_product.bld_no_required", "BLD 号不能为空。", field="bld_no"
        )
    if len(bld_no) > 80:
        raise CustomerProductValidationError(
            "customer_product.bld_no_too_long", "BLD 号不能超过 80 个字符。", field="bld_no"
        )
    if not _BLD_NO_PATTERN.fullmatch(bld_no):
        raise CustomerProductValidationError(
            "customer_product.invalid_bld_no", "BLD 号只能包含字母、数字和常见分隔符。", field="bld_no"
        )
    return bld_no


def clean_product_code(value: object) -> str:
    code = " ".join(str(value or "").split())
    if len(code) > 120:
        raise CustomerProductValidationError(
            "customer_product.code_too_long", "客户产品编码不能超过 120 个字符。", field="customer_product_code"
        )
    return code


def clean_product_name(value: object) -> str:
    name = " ".join(str(value or "").split())
    if len(name) > 200:
        raise CustomerProductValidationError(
            "customer_product.name_too_long", "客户产品名称不能超过 200 个字符。", field="customer_product_name"
        )
    return name


def clean_revision_label(value: object) -> str:
    revision_label = " ".join(str(value or "").split())
    if len(revision_label) > 60:
        raise CustomerProductValidationError(
            "customer_drawing.revision_label_too_long", "版本代号不能超过 60 个字符。", field="revision_label"
        )
    return revision_label


def clean_note(value: object) -> str:
    note = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(note) > 2000:
        raise CustomerProductValidationError(
            "customer_drawing.note_too_long", "版本备注不能超过 2000 个字符。", field="note"
        )
    return note
