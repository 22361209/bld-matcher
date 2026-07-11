"""Compatibility facade for material persistence operations."""

from .excel_import import bootstrap_materials_from_excel, import_materials_from_excel
from .item_store import (
    count_material_items,
    deactivate_material_item,
    get_material_item,
    list_material_items,
    material_item_stats,
    rows_for_material_sheet,
    upsert_material_item,
)

__all__ = [
    "bootstrap_materials_from_excel",
    "count_material_items",
    "deactivate_material_item",
    "get_material_item",
    "import_materials_from_excel",
    "list_material_items",
    "material_item_stats",
    "rows_for_material_sheet",
    "upsert_material_item",
]
