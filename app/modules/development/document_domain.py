from __future__ import annotations

from uuid import UUID


class DevelopmentDocumentError(ValueError):
    def __init__(self, code: str, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def identity(value: object) -> str:
    try:
        text = str(value)
        if str(UUID(text)) != text:
            raise ValueError
        return text
    except (ValueError, TypeError, AttributeError) as exc:
        raise DevelopmentDocumentError("development.document_identity", "资料标识无效。") from exc


def required_text(value: object, label: str, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        raise DevelopmentDocumentError("development.document_invalid", f"{label}不能为空且最多 {limit} 字。")
    return text
