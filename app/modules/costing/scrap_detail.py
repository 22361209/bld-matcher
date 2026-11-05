"""Read the scrap evidence already attached to a cost sheet, without repricing it."""
from __future__ import annotations

from .import_values import number


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _cell(snapshot, column):
    raw = _mapping(snapshot.get("raw")).get(column)
    formula = raw if isinstance(raw, str) and raw.startswith("=") else ""
    cached = _mapping(snapshot.get("cached")).get(column)
    row = snapshot.get("row")
    return {
        "address": f"{snapshot['sheet']}!{column}{row}" if snapshot.get("sheet") and row else "",
        "formula": formula,
        "value": number(cached if formula or raw is None else raw),
    }


def scrap_detail(source_info):
    source = _mapping(source_info)
    pivot = _mapping(source.get("pivot"))
    pieces = []
    rows = source.get("scrap_reference_rows")
    for item in rows if isinstance(rows, list) else ():
        if not isinstance(item, dict):
            continue
        snapshot = _mapping(item.get("source_info"))
        pieces.append({
            **{key: number(item.get(key)) for key in ("gross_weight", "net_weight", "scrap_weight", "unit_price", "amount")},
            "name": item.get("name"), "note": item.get("review_note"),
            "weight_cell": _cell(snapshot, "F"), "amount_cell": _cell(snapshot, "H"),
        })
    known = sum(piece["amount"] is not None for piece in pieces)
    return {
        "model": _mapping(pivot.get("raw")).get("A"),
        "original_credit": _cell(pivot, "F"), "original_theory": _cell(pivot, "K"),
        "pieces": pieces, "missing_amounts": len(pieces) - known,
        "known_total": number(source.get("scrap_known_total")) if known else None,
    }
