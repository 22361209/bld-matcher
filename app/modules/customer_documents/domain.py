from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class CustomerDocumentCategory(StrEnum):
    LABEL = "label"
    OUTER_BOX = "outer_box"
    INNER_PACKAGING = "inner_packaging"
    PACKAGING_EXAMPLE = "packaging_example"
    DELIVERY_NOTE = "delivery_note"
    PI = "pi"
    PL = "pl"
    CI = "ci"
    OTHER = "other"


CATEGORY_LABELS: dict[str, str] = {
    CustomerDocumentCategory.LABEL: "标签要求",
    CustomerDocumentCategory.OUTER_BOX: "外箱要求",
    CustomerDocumentCategory.INNER_PACKAGING: "内袋/包装要求",
    CustomerDocumentCategory.PACKAGING_EXAMPLE: "包装示例",
    CustomerDocumentCategory.DELIVERY_NOTE: "出库单模板",
    CustomerDocumentCategory.PI: "PI 模板",
    CustomerDocumentCategory.PL: "PL 模板",
    CustomerDocumentCategory.CI: "CI 模板",
    CustomerDocumentCategory.OTHER: "其他",
}

CUSTOMER_DOCUMENT_CATEGORIES: tuple[dict[str, str], ...] = tuple(
    {"value": category.value, "label": CATEGORY_LABELS[category.value]} for category in CustomerDocumentCategory
)

PREVIEWABLE_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/plain",
        "text/csv",
    }
)


class CustomerDocumentValidationError(ValueError):
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
class CustomerDocumentFile:
    id: int
    sync_id: str
    group_id: int
    version_no: int
    original_name: str
    storage_path: str = field(repr=False)
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    sha256: str = ""
    created_by: str = ""
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
            "original_name": self.original_name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "previewable": self.previewable,
        }


@dataclass(frozen=True, slots=True)
class CustomerDocumentGroup:
    id: int
    customer_id: int
    sync_id: str
    category: str
    title: str
    description: str
    language: str
    current_version: int
    archived: bool
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str
    files: tuple[CustomerDocumentFile, ...] = ()

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, CATEGORY_LABELS[CustomerDocumentCategory.OTHER])

    @property
    def current_files(self) -> tuple[CustomerDocumentFile, ...]:
        return tuple(item for item in self.files if item.version_no == self.current_version)

    @property
    def versions(self) -> tuple[int, ...]:
        return tuple(sorted({item.version_no for item in self.files}, reverse=True))

    def payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "sync_id": self.sync_id,
            "category": self.category,
            "category_label": self.category_label,
            "title": self.title,
            "description": self.description,
            "language": self.language,
            "current_version": self.current_version,
            "archived": self.archived,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files": [item.payload() for item in self.files],
            "current_files": [item.payload() for item in self.current_files],
            "versions": list(self.versions),
        }


@dataclass(frozen=True, slots=True)
class CustomerDocumentSummary:
    group_count: int = 0
    file_count: int = 0
    current_file_count: int = 0

    def payload(self) -> dict[str, int]:
        return {
            "group_count": self.group_count,
            "file_count": self.file_count,
            "current_file_count": self.current_file_count,
        }


def clean_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise CustomerDocumentValidationError("customer_document.title_required", "资料标题不能为空。", field="title")
    if len(title) > 120:
        raise CustomerDocumentValidationError(
            "customer_document.title_too_long", "资料标题不能超过 120 个字符。", field="title"
        )
    return title


def clean_description(value: object) -> str:
    description = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(description) > 4000:
        raise CustomerDocumentValidationError(
            "customer_document.description_too_long", "文字说明不能超过 4000 个字符。", field="description"
        )
    return description


def clean_category(value: object) -> str:
    category = str(value or "").strip().lower()
    if category not in CATEGORY_LABELS:
        raise CustomerDocumentValidationError(
            "customer_document.invalid_category", "请选择有效的资料分类。", field="category"
        )
    return category


def clean_language(value: object) -> str:
    language = str(value or "zh-CN").strip() or "zh-CN"
    if len(language) > 32 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", language):
        raise CustomerDocumentValidationError(
            "customer_document.invalid_language", "语言标识格式不正确。", field="language"
        )
    return language
