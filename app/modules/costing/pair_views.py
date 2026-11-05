"""Web forms for previewing existing left/right cost sheets before pairing."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.security import actor_name

from .domain import CostingValidationError
from .pair_catalog import CostingPairCatalog
from .web_support import costing_endpoint, service


def _version(value: str | None) -> int:
    if not value or not value.isascii() or not value.isdecimal() or len(value) > 12:
        raise CostingValidationError("请重新打开预览，核对成本单版本。")
    return int(value)


def candidates():
    query = request.args.get("q", "").strip()
    rows = CostingPairCatalog(service()).candidates()
    if query:
        rows = [row for row in rows if any(query.casefold() in row[key].casefold() for key in ("code", "other_code"))]
    return render_template("costing_pairs.html", candidates=rows, query=query)


def mirror_selected():
    selected = []
    for value in request.form.getlist("candidate"):
        record_id, _, version = value.rpartition(":")
        selected.append({"id": record_id, "version": _version(version)})
    if not selected:
        raise CostingValidationError("请先选择要建立另一侧的型号。")
    count = CostingPairCatalog(service()).mirror_selected(selected, actor=actor_name())
    flash(f"已建立 {count} 对左右件成本单。", "success")
    return redirect(url_for(costing_endpoint() + ".pairs", q=request.form.get("q", "")))


def review(record_id: str):
    preview = CostingPairCatalog(service()).existing(record_id)
    return render_template("costing_pair_review.html", **preview)


def apply_review(record_id: str):
    catalog = CostingPairCatalog(service())
    version = _version(request.form.get("version"))
    row_ids = request.form.getlist("row")
    if request.form.get("action") == "link":
        matches = [
            {"id": row_id, "other_id": request.form.get(f"target-{row_id}", ""), "mode": request.form.get(f"kind-{row_id}", "")}
            for row_id in row_ids if request.form.get(f"target-{row_id}")
        ]
        catalog.link_existing(record_id, version=version, other_version=_version(request.form.get("other_version")),
                              matches=matches, actor=actor_name())
    elif request.form.get("action") == "mirror":
        rows = []
        for row_id in row_ids:
            row = {"id": row_id, "pair_kind": request.form.get(f"kind-{row_id}", "")}
            if row["pair_kind"] == "handed":
                row.update(peer_product_code=request.form.get(f"product-{row_id}", ""),
                           peer_part_code=request.form.get(f"part-{row_id}", ""))
            rows.append(row)
        catalog.mirror_selected([{"id": record_id, "version": version, "rows": rows}], actor=actor_name())
    else:
        raise CostingValidationError("请重新预览要配对的型号。")
    flash("左右件已配对。", "success")
    return redirect(url_for(costing_endpoint() + ".edit", record_id=record_id))
