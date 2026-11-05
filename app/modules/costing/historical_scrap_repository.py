"""The formal cost and material repositories share a transaction during historical backfill."""
from __future__ import annotations

import sqlite3

from app.modules.materials.repository import SQLiteMaterialRepository

from .repository import SQLiteCostingUnitOfWork


class HistoricalScrapUnitOfWork(SQLiteCostingUnitOfWork):
    def __enter__(self):
        super().__enter__()
        assert self.connection is not None
        self.connection.row_factory = sqlite3.Row
        self.materials = SQLiteMaterialRepository(self.connection)
        return self
