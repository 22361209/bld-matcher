"""SQL expressions shared by material search, column filters and row counts."""
from __future__ import annotations

from collections.abc import Mapping

from .specification import _parse_material_spec_query


UNIT_WEIGHT_SQL = "(width * length * 7.85 * thickness / pieces / 1000000.0)"
_SCRAP_WEIGHT_SQL = f"MAX(0, {UNIT_WEIGHT_SQL} - CAST(net_weight AS REAL))"
_SCRAP_KNOWN_SQL = f"net_weight IS NOT NULL AND {UNIT_WEIGHT_SQL} > 0"

MATERIAL_COLUMN_EXPRESSIONS = {
    "model": "COALESCE(model, '')",
    "code": "COALESCE(code, '')",
    "category": "COALESCE(category, '')",
    "car": "COALESCE(car, '')",
    "part": "COALESCE(part, '')",
    "material_grade": "COALESCE(material_grade, '')",
    "spec_text": "COALESCE(spec_text, '')",
    "pieces": "printf('%.2f', pieces)",
    "unit_weight": f"printf('%.2f', {UNIT_WEIGHT_SQL})",
    "net_weight": "COALESCE(net_weight, '')",
    "scrap_weight": f"CASE WHEN {_SCRAP_KNOWN_SQL} THEN printf('%.4f', {_SCRAP_WEIGHT_SQL}) ELSE '' END",
    "scrap_percent": f"CASE WHEN {_SCRAP_KNOWN_SQL} THEN printf('%.2f', 100 * {_SCRAP_WEIGHT_SQL} / {UNIT_WEIGHT_SQL}) ELSE '' END",
    "active": "CASE WHEN active = 1 THEN 'active' ELSE 'inactive' END",
}
EMPTY_FILTER_VALUE = "__blank__"


def _material_spec_search(tokens: list[float]) -> tuple[str, list[object]]:
    if not tokens:
        return "", []
    epsilon = 0.000001

    def equal(field: str) -> str:
        return f"ABS({field} - ?) < ?"

    if len(tokens) == 1:
        value = tokens[0]
        return (
            f"({equal('thickness')} OR {equal('width')} OR {equal('length')})",
            [value, epsilon, value, epsilon, value, epsilon],
        )
    if len(tokens) == 2:
        first, second = tokens
        return (
            f"(({equal('thickness')} AND {equal('width')}) OR "
            f"({equal('thickness')} AND {equal('length')}) OR "
            f"({equal('width')} AND {equal('length')}))",
            [first, epsilon, second, epsilon] * 3,
        )
    first, second, third = tokens
    return (
        f"({equal('thickness')} AND {equal('width')} AND {equal('length')})",
        [first, epsilon, second, epsilon, third, epsilon],
    )


def material_filter_clauses(
    query: str = "",
    include_inactive: bool = False,
    only_inactive: bool = False,
    column_filters: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if only_inactive:
        clauses.append("active = 0")
    elif not include_inactive:
        clauses.append("active = 1")
    if query.strip():
        key = f"%{query.strip()}%"
        search = ["(model LIKE ? OR code LIKE ? OR category LIKE ? OR car LIKE ? OR part LIKE ? OR spec_text LIKE ? OR material_grade LIKE ?)"]
        search_params: list[object] = [key] * 7
        spec_clause, spec_params = _material_spec_search(_parse_material_spec_query(query))
        if spec_clause:
            search.append(spec_clause)
            search_params.extend(spec_params)
        clauses.append("(" + " OR ".join(search) + ")")
        params.extend(search_params)
    for field, values in (column_filters or {}).items():
        expression = MATERIAL_COLUMN_EXPRESSIONS.get(field)
        if not expression:
            continue
        regular = [value for value in values if value != EMPTY_FILTER_VALUE]
        predicates: list[str] = []
        if regular:
            predicates.append(f"{expression} IN ({', '.join('?' for _ in regular)})")
            params.extend(regular)
        if EMPTY_FILTER_VALUE in values:
            predicates.append(f"{expression} = ''")
        if predicates:
            clauses.append("(" + " OR ".join(predicates) + ")")
    return clauses, params
