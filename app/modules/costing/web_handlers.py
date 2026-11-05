"""Shared HTTP workflows; route adapters supply their own authentication boundary."""
from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.security import actor_name as actor, can, wants_json_response

from .domain import CostingConflict, CostingValidationError
from .editor_choices import editor_choices
from .grid import (
    COLUMN_FILTER_FIELDS, FULL_LIST_RECORD_FIELDS, PRICE_LIST_COLUMNS, PRICE_LIST_RECORD_FIELDS, normalize_column_filters,
)
from .pair_codes import mirror_code
from .scrap_detail import scrap_detail
from .web_support import costing_endpoint, import_owner, imports, service, weights


EDIT_FIELDS = {"code", "kind", "name", "brand", "models", "remark", "approved_cost", "scrap_credit", "theoretical_scrap",
               "items", "suggested_tax_price_override", "create_pair", "peer_version",
               "scrap_mode", "scrap_unit_price", "scrap_credit_override"}
ITEM_FIELDS = {"id", "category", "name", "product_code", "material_attribute", "part_code", "supplier", "remark", "mode", "amount", "unit_price",
               "quantity", "weight", "processing_fee", "length", "width", "height", "strap_rate", "review_note", "pair_kind",
               "peer_product_code", "peer_part_code", "weight_choice", "scrap_expected"}


def _body() -> tuple[dict, dict]:
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        raise CostingValidationError("请提交有效的成本单。")
    data = {key: value for key, value in raw.items() if key in EDIT_FIELDS}
    if "items" in data:
        if not isinstance(data["items"], list):
            raise CostingValidationError("成本明细格式不正确。", "items")
        data["items"] = [{key: value for key, value in item.items() if key in ITEM_FIELDS}
                         if isinstance(item, dict) else item for item in data["items"]]
        weights().apply(data["items"])
    return raw, data


def weight_lookup():
    lookup = weights()
    return jsonify(ok=True, available=lookup.available,
                   candidates=lookup.candidates(request.args.get("code"), request.args.get("kind", "")))


def index():
    full_list = can("view_cost_list_full")
    details = can("view_cost_details")
    visible_columns = COLUMN_FILTER_FIELDS if full_list else PRICE_LIST_COLUMNS
    filters = {key: request.args.get(key, "") for key in ("q", "kind", "status")}
    filters["sort"] = request.args.get("sort", "code")
    if not full_list:
        filters["status"] = ""
        if filters["sort"] == "updated":
            filters["sort"] = "code"
    column_filters = normalize_column_filters({
        field: request.args.getlist(f"cf_{field}") for field in visible_columns if f"cf_{field}" in request.args
    })
    try:
        page = int(request.args.get("page", "1"))
    except ValueError as exc:
        raise CostingValidationError("页码必须是整数。", "page") from exc
    result = service().list(q=filters["q"], kind=filters["kind"], status=filters["status"],
                            sort=filters["sort"], page=page, column_filters=column_filters, summary_only=not details)
    if not details:
        allowed = FULL_LIST_RECORD_FIELDS if full_list else PRICE_LIST_RECORD_FIELDS
        result["records"] = [{key: row.get(key) for key in allowed} for row in result["records"]]
    if wants_json_response():
        return jsonify(ok=True, result=result)
    options = service().filter_options(q=filters["q"], kind=filters["kind"], status=filters["status"],
                                      column_filters=column_filters, fields=visible_columns)
    return render_template("costing_list.html", result=result, filters=filters, column_filters=column_filters,
                           column_filter_options=options,
                           column_filter_query={f"cf_{field}": list(values) for field, values in column_filters.items()})


def new():
    copy_id = request.args.get("copy", "")
    if copy_id:
        original = service().get(copy_id)
        # A copy is priced as a new record, even when its source has historical totals.
        record = service().calculate(original)
        for key in ("source_info", "pair", "peer_code", "peer_version", "peer_has_manual_price"):
            record.pop(key, None)
        record["code"] = ""
        for item in record["items"]:
            for key in ("id", "source_info", "pair_kind", "peer_item_id"):
                item.pop(key, None)
            item.setdefault("material_attribute", "")
            if item.get("weight_source"):
                item["weight_choice"] = weights().copy_token(item["weight_source"])
        copy_from = original["code"]
    else:
        record = {"code": "", "kind": "arm", "name": "", "brand": "", "models": "", "remark": "",
                  "approved_cost": None, "scrap_credit": "0", "theoretical_scrap": None, "items": [],
                  "scrap_mode": "auto", "scrap_unit_price": "2", "scrap_credit_override": None}
        copy_from = ""
    return render_template("costing_edit.html", record=record, is_new=True, can_edit=True,
                           copy_from=copy_from, revisions=[], scrap=scrap_detail(record.get("source_info")),
                           references=editor_choices(imports().references()))


def edit(record_id: str):
    record = service().get(record_id)
    if wants_json_response():
        return jsonify(ok=True, record=record)
    return render_template("costing_edit.html", record=record, is_new=False, can_edit=can("manage_costs"),
                           revisions=service().revisions(record_id), candidate_code=mirror_code(record["code"]), copy_from="",
                           scrap=scrap_detail(record.get("source_info")), references=editor_choices(imports().references()))


def preview():
    raw, data = _body()
    # Preview is side-effect free and accepts an unnamed draft while the user enters its code.
    if not data.get("code"):
        data["code"] = "待填写型号"
    before = None
    if raw.get("id"):
        before = service().get(str(raw["id"]))
        if raw.get("version") != before["version"]:
            raise CostingConflict("成本单已被其他操作修改，请保留输入并重新打开核对。")
        data = {**before, **data}
    result = service().calculate(data, previous=before)
    if not raw.get("code"):
        result["code"] = ""
        result["status"] = "draft"
    return jsonify(ok=True, record=result)


def create():
    _raw, data = _body()
    record = service().create(data, actor=actor())
    return jsonify(ok=True, record=record, url=url_for(costing_endpoint() + ".edit", record_id=record["id"])), 201


def update(record_id: str):
    raw, data = _body()
    version = raw.get("version")
    if type(version) is not int or version < 1:
        raise CostingValidationError("请提供当前成本单版本。", "version")
    record = service().update(record_id, data, expected_version=version, actor=actor())
    return jsonify(ok=True, record=record, url=url_for(costing_endpoint() + ".edit", record_id=record["id"]))


def import_page():
    return render_template("costing_import.html", preview=None, preview_token="")


def preview_import():
    preview, token = imports().preview(request.files.get("file"), owner=import_owner())
    if wants_json_response():
        return jsonify(ok=True, preview=preview, preview_token=token)
    return render_template("costing_import.html", preview=preview, preview_token=token)


def apply_import():
    raw = request.get_json(silent=True) if request.is_json else request.form
    token = raw.get("preview_token", "") if raw else ""
    result = imports().apply(token, owner=import_owner(), actor=actor())
    if wants_json_response() or request.is_json:
        return jsonify(ok=True, **result)
    flash(f"已导入 {result['imported']} 个型号。", "success")
    return redirect(url_for(costing_endpoint() + ".index"))
