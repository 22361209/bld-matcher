from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .domain import tolerance_only
from .importer import load_tube_rows


class TubeService:
    def __init__(self, unit_of_work_factory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def list_items(self, *, filters: Mapping[str, object], limit: int, offset: int) -> dict[str, object]:
        with self.unit_of_work_factory() as unit_of_work:
            raw_inner_tolerance_options = unit_of_work.repository.value_counts("inner_diameter_tolerance")
            expanded_filters = dict(filters)
            selected_inner_tolerances = tuple(
                str(value) for value in cast(tuple[object, ...], filters.get("inner_tolerances", ()))
            )
            if selected_inner_tolerances:
                selected = set(selected_inner_tolerances)
                expanded_filters["inner_tolerances"] = tuple(
                    str(option["value"])
                    for option in raw_inner_tolerance_options
                    if tolerance_only(option["value"]) in selected
                ) or (None,)
            total = unit_of_work.repository.count(filters=expanded_filters)
            records = unit_of_work.repository.list(filters=expanded_filters, limit=limit, offset=offset)
            counts = unit_of_work.repository.type_counts()
            blank_length_options = unit_of_work.repository.value_counts("blank_length_text")
            inner_tolerance_options = self._group_inner_tolerance_options(raw_inner_tolerance_options)
            purchase_base_options = unit_of_work.repository.value_counts("purchase_base")
            tolerance_options = unit_of_work.repository.number_counts("tolerance_mm")
            consumption_options = unit_of_work.repository.number_counts("consumption_mm")
        return {
            "records": records,
            "total": total,
            "counts": counts,
            "blank_length_options": blank_length_options,
            "inner_tolerance_options": inner_tolerance_options,
            "purchase_base_options": purchase_base_options,
            "tolerance_options": tolerance_options,
            "consumption_options": consumption_options,
        }

    @staticmethod
    def _group_inner_tolerance_options(raw_options: list[dict[str, object]]) -> list[dict[str, object]]:
        grouped: dict[str, int] = {}
        for option in raw_options:
            tolerance = tolerance_only(option["value"])
            if tolerance:
                grouped[tolerance] = grouped.get(tolerance, 0) + int(cast(int, option["count"]))
        return [
            {"value": tolerance, "label": tolerance, "count": count}
            for tolerance, count in sorted(grouped.items())
        ]

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
