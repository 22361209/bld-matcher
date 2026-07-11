from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from app.locks import ImportLockError, import_lock
from app.quote_import import decode_rows, encode_rows, parse_quote_import_file

from .ports import ImportLockBusyError


class ExcelQuoteImportAdapter:
    def parse(self, path: Path, *, customer_name: str, currency: str) -> dict:
        return parse_quote_import_file(path, customer_name=customer_name, currency=currency)

    def encode(self, rows: list[dict]) -> str:
        return encode_rows(rows)

    def decode(self, payload: str) -> list[dict]:
        return decode_rows(payload)


class FileImportLockAdapter:
    @contextmanager
    def __call__(self, owner: str, purpose: str):
        try:
            with import_lock(owner, purpose):
                yield
        except ImportLockError as exc:
            raise ImportLockBusyError(str(exc)) from exc
