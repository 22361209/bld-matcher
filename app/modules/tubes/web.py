from __future__ import annotations

from typing import cast
from urllib.parse import urlencode
from math import ceil

from flask import flash, redirect, render_template, request, url_for

from app.security import actor_name, login_required, permission_required

from .domain import TUBE_TYPES, spec_display_lines, tolerance_only
from .factory import get_tube_service


TUBE_PAGE_SIZE = 100


def _number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _tube_filters() -> dict[str, object]:
    return {
        "query": request.args.get("q", "").strip(),
        "tube_types": tuple(value for value in TUBE_TYPES if value in request.args.getlist("type")),
        "blank_lengths": tuple(value for value in request.args.getlist("blank_length") if value),
        "inner_tolerances": tuple(value for value in request.args.getlist("inner_tolerance") if value),
        "purchase_bases": tuple(value for value in request.args.getlist("purchase_base") if _number(value) is not None),
        "materials": tuple(value for value in request.args.getlist("material") if value == "—"),
        "tolerances": tuple(value for value in (_number(item) for item in request.args.getlist("tolerance")) if value is not None),
        "consumptions": tuple(value for value in (_number(item) for item in request.args.getlist("consumption")) if value is not None),
        "weight_eq": _number(request.args.get("weight_eq")),
        "weight_min": _number(request.args.get("weight_min")),
        "weight_max": _number(request.args.get("weight_max")),
        "outer_diameter": _number(request.args.get("outer_diameter")),
        "inner_diameter": _number(request.args.get("inner_diameter")),
    }


def _page_url(filters: dict[str, object], page: int) -> str:
    pairs: list[tuple[str, object]] = []
    if filters["query"]:
        pairs.append(("q", filters["query"]))
    for key, parameter in (
        ("tube_types", "type"),
        ("blank_lengths", "blank_length"),
        ("inner_tolerances", "inner_tolerance"),
        ("purchase_bases", "purchase_base"),
        ("materials", "material"),
        ("tolerances", "tolerance"),
        ("consumptions", "consumption"),
    ):
        pairs.extend((parameter, value) for value in cast(tuple[object, ...], filters[key]))
    for key in ("weight_eq", "weight_min", "weight_max", "outer_diameter", "inner_diameter"):
        if filters[key] is not None:
            pairs.append((key, filters[key]))
    if page > 1:
        pairs.append(("page", page))
    query = urlencode(pairs)
    return f"{url_for('tube_items')}?{query}#tube-results" if query else f"{url_for('tube_items')}#tube-results"


def _pagination(filters: dict[str, object], page: int, total: int) -> dict[str, object]:
    total_pages = max(1, ceil(total / TUBE_PAGE_SIZE))
    current_page = min(page, total_pages)
    window = {1, total_pages, current_page - 1, current_page, current_page + 1}
    links: list[dict[str, object]] = []
    previous = 0
    for candidate in sorted(value for value in window if 1 <= value <= total_pages):
        if previous and candidate - previous > 1:
            links.append({"gap": True})
        links.append({"page": candidate, "url": _page_url(filters, candidate), "current": candidate == current_page})
        previous = candidate
    return {"page": current_page, "total_pages": total_pages, "links": links, "has_prev": current_page > 1, "has_next": current_page < total_pages, "prev_url": _page_url(filters, current_page - 1) if current_page > 1 else "", "next_url": _page_url(filters, current_page + 1) if current_page < total_pages else ""}


def register(app) -> None:
    @app.get("/tubes")
    @login_required
    def tube_items():
        filters = _tube_filters()
        page = max(1, request.args.get("page", 1, type=int) or 1)
        result = get_tube_service().list_items(
            filters=filters,
            limit=TUBE_PAGE_SIZE,
            offset=(page - 1) * TUBE_PAGE_SIZE,
        )
        total = cast(int, result["total"])
        pagination = _pagination(filters, page, total)
        return render_template(
            "tubes.html",
            tube_items=result["records"],
            tube_types=TUBE_TYPES,
            selected_types=filters["tube_types"],
            selected_blank_lengths=filters["blank_lengths"],
            selected_inner_tolerances=filters["inner_tolerances"],
            selected_purchase_bases=filters["purchase_bases"],
            selected_materials=filters["materials"],
            selected_tolerances=filters["tolerances"],
            selected_consumptions=filters["consumptions"],
            weight_eq=filters["weight_eq"],
            weight_min=filters["weight_min"],
            weight_max=filters["weight_max"],
            outer_diameter=filters["outer_diameter"],
            inner_diameter=filters["inner_diameter"],
            type_counts=result["counts"],
            blank_length_options=result["blank_length_options"],
            inner_tolerance_options=result["inner_tolerance_options"],
            purchase_base_options=result["purchase_base_options"],
            tolerance_options=result["tolerance_options"],
            consumption_options=result["consumption_options"],
            spec_display_lines=spec_display_lines,
            tolerance_only=tolerance_only,
            query=filters["query"],
            total=total,
            pagination=pagination,
        )

    @app.get("/tubes/new")
    @permission_required("manage_materials")
    def new_tube_item():
        return render_template("tube_form.html", item=None, tube_types=TUBE_TYPES)

    @app.get("/tubes/<int:item_id>/edit")
    @permission_required("manage_materials")
    def edit_tube_item(item_id: int):
        item = get_tube_service().get_item(item_id)
        if item is None:
            flash("管件不存在。", "error")
            return redirect(url_for("tube_items"))
        return render_template("tube_form.html", item=item, tube_types=TUBE_TYPES)

    @app.post("/tubes/save")
    @permission_required("manage_materials")
    def save_tube_item():
        data: dict[str, object] = {
            "id": request.form.get("id", ""),
            "code": request.form.get("code", ""),
            "tube_type": request.form.get("tube_type", ""),
            "spec_text": request.form.get("spec_text", ""),
            "weight_kg": request.form.get("weight_kg", ""),
            "tolerance_mm": request.form.get("tolerance_mm", ""),
            "consumption_mm": request.form.get("consumption_mm", ""),
            "outer_diameter_mm": request.form.get("outer_diameter_mm", ""),
            "inner_diameter_mm": request.form.get("inner_diameter_mm", ""),
            "blank_length_text": request.form.get("blank_length_text", ""),
            "inner_diameter_tolerance": request.form.get("inner_diameter_tolerance", ""),
            "purchase_base": request.form.get("purchase_base", "1"),
            "borrowed_from": request.form.get("borrowed_from", ""),
            "note": request.form.get("note", ""),
            "active": request.form.get("active", "1"),
        }
        if "borrowed_codes" in request.form:
            data["borrowed_codes"] = request.form.get("borrowed_codes", "")
        try:
            get_tube_service().save(data, actor=actor_name())
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("new_tube_item"))
        flash("管件已保存。", "success")
        return redirect(url_for("tube_items", q=data["code"]))
