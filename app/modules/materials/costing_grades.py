"""Master-data projection used to resolve stamping prices under the cost edit lock."""
from __future__ import annotations

import sqlite3

MATERIAL_GRADES = ("SPHC", "SAPH440")


def read_costing_grade_rows(connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute(
        "SELECT sync_id, model, code, part, spec_text, material_grade FROM material_items "
        "WHERE active=1 ORDER BY sync_id"
    )]
