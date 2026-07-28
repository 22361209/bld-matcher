from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Customer:
    id: int
    name: str
    sync_id: str
    created_at: str
    updated_at: str


class CustomerValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def clean_customer_name(value: object) -> str:
    return " ".join(str(value or "").split())
