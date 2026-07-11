from __future__ import annotations

from functools import lru_cache

from app.config import DATA_DIR, DB_PATH, MATERIAL_DATA_PATH, MATERIAL_TEMPLATE_PATH
from app.database import bootstrap_materials_from_excel

from .infrastructure import MaterialFileAdapter
from .repository import SQLiteMaterialUnitOfWork
from .service import MaterialService


@lru_cache(maxsize=1)
def get_material_service() -> MaterialService:
    return MaterialService(
        lambda: SQLiteMaterialUnitOfWork(DB_PATH),
        lambda: bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH),
        MaterialFileAdapter(
            data_dir=DATA_DIR,
            material_data_path=MATERIAL_DATA_PATH,
            template_path=MATERIAL_TEMPLATE_PATH,
            drawing_dir=DATA_DIR / "material_drawings",
        ),
    )
