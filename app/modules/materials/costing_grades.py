"""Master-data projection used to resolve stamping prices under the cost edit lock."""
from __future__ import annotations

import sqlite3

MATERIAL_GRADES = ("SPHC", "SAPH440")


def read_costing_grade_rows(connection: sqlite3.Connection) -> list[dict]:
    from .bom_query import MaterialBOM

    fields = ("sync_id", "model", "code", "part", "spec_text", "material_grade")
    rows = []
    for row in MaterialBOM(connection).active_stamps():
        projection = {k: row[k] for k in fields}
        if row.get("master"):
            projection.update(master=row["master"], aliases=row["aliases"])
        rows.append(projection)
    return rows
