from __future__ import annotations

from app.config import DB_PATH

from .infrastructure import ExcelQuoteImportAdapter, FileImportLockAdapter
from .repository import SQLiteQuoteUnitOfWork
from .service import QuoteService


def get_quote_service() -> QuoteService:
    return QuoteService(
        lambda: SQLiteQuoteUnitOfWork(DB_PATH),
        ExcelQuoteImportAdapter(),
        FileImportLockAdapter(),
    )
