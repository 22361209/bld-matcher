from __future__ import annotations

from functools import lru_cache

from app.config import DB_PATH, UPLOAD_DIR

from .service import CostingService
from .trial_import_service import TrialImportService


@lru_cache(maxsize=1)
def get_costing_service() -> CostingService:
    return CostingService(DB_PATH)


def get_costing_import_service(secret: str) -> TrialImportService:
    return TrialImportService(UPLOAD_DIR / "costing", secret, get_costing_service())
