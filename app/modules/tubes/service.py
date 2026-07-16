from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .importer import load_tube_rows


class TubeService:
    def __init__(self, unit_of_work_factory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def list_items(self, *, filters: Mapping[str, object], limit: int, offset: int) -> dict[str, object]:
        with self.unit_of_work_factory() as unit_of_work:
            total = unit_of_work.repository.count(filters=filters)
            records = unit_of_work.repository.list(filters=filters, limit=limit, offset=offset)
            counts = unit_of_work.repository.type_counts()
            tolerance_options = unit_of_work.repository.number_counts("tolerance_mm")
            consumption_options = unit_of_work.repository.number_counts("consumption_mm")
        return {"records": records, "total": total, "counts": counts, "tolerance_options": tolerance_options, "consumption_options": consumption_options}

    def get_item(self, item_id: int) -> dict[str, object] | None:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.get(item_id)

    def save(self, data: Mapping[str, object], *, actor: str) -> int:
        with self.unit_of_work_factory() as unit_of_work:
            item_id = unit_of_work.repository.save(data, actor=actor)
            unit_of_work.commit()
            return item_id

    def import_workbook(self, workbook_path: Path, *, actor: str) -> int:
        rows = load_tube_rows(workbook_path)
        with self.unit_of_work_factory() as unit_of_work:
            imported = unit_of_work.repository.import_rows(rows, actor=actor)
            unit_of_work.commit()
            return imported
