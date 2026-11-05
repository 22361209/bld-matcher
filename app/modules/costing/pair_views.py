"""Web forms for previewing existing left/right cost sheets before pairing."""
from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.security import actor_name, can, wants_json_response

from .domain import CostingValidationError
from .pair_catalog import CostingPairCatalog
from .web_support import costing_endpoint, error_response, service


def _version(value: str | None) -> int:
    if not value or not value.isascii() or not value.isdecimal() or len(value) > 12:
        raise CostingValidationError("请重新打开预览，核对成本单版本。")
    return int(value)


def candidates():
    query = request.args.get("q", "").strip()
    can_manage = can("manage_costs")
    status = request.args.get("status", "") if can_manage else "paired"
    if status not in ("", "paired", "pending"):
        raise CostingValidationError("配对状态无效。")
    rows = CostingPairCatalog(service()).candidates(include_paired=True)
    if status:
        rows = [row for row in rows if row["paired"] == (status == "paired")]
    if query:
        rows = [row for row in rows if any(query.casefold() in row[key].casefold() for key in ("code", "other_code"))]
    return render_template("costing_pairs.html", candidates=rows, query=query, status=status, can_manage=can_manage)


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
    editable = can("manage_costs")
    if not editable and not preview["paired"]:
        return error_response("建立左右件配对需要成本维护权限。", 403)
    peers = preview["other"]["items"] if preview["other"] else []
    peer_positions = {item["id"]: (index, item) for index, item in enumerate(peers, 1)}
    return render_template("costing_pair_review.html", editable=editable, peer_positions=peer_positions, **preview)


def apply_review(record_id: str):
    catalog = CostingPairCatalog(service())
    version = _version(request.form.get("version"))
    row_ids = request.form.getlist("row")
    action = request.form.get("action")
    if action in ("link", "revise"):
        matches = []
        for row_id in row_ids:
            target_key = f"target-{row_id}"
            if action == "revise" and target_key not in request.form:
                raise CostingValidationError("请提交完整的明细对应关系。")
            target = request.form.get(target_key, "")
            if target or action == "revise":
                mode = request.form.get(f"kind-{row_id}", "") if target else "local"
                matches.append({"id": row_id, "other_id": target, "mode": mode})
        save = catalog.revise_existing if action == "revise" else catalog.link_existing
        save(record_id, version=version, other_version=_version(request.form.get("other_version")),
             matches=matches, actor=actor_name())
    elif action == "mirror":
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
    flash("已保存明细配对关系。" if action == "revise" else "左右件已配对。", "success")
    if wants_json_response():
        return jsonify(ok=True)
    return redirect(url_for(costing_endpoint() + ".pair_review", record_id=record_id))
