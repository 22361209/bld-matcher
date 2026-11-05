"""Authenticated review and application of the cost sheets' stamping-price changes."""
from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from itsdangerous import BadData, URLSafeTimedSerializer

from app.security import actor_name, permission_required, wants_json_response

from .domain import CostingConflict, CostingValidationError
from .material_prices import MaterialPriceService
from .web_support import import_owner, register_error_handlers, service


material_prices = Blueprint("costing_material_prices", __name__, url_prefix="/costs/material-prices")
register_error_handlers(material_prices)


def _signer():
    secret = current_app.secret_key
    if not secret:
        raise RuntimeError("缺少应用签名配置。")
    return URLSafeTimedSerializer(secret, salt="costing-stamping-price-v1")


def _form(catalog, *, grade="SPHC", price="", error=None):
    if error and wants_json_response():
        return jsonify(ok=False, error=error.message), 409 if isinstance(error, CostingConflict) else 422
    return render_template("costing_material_prices.html", catalog=catalog.catalog(), grade=grade, price=price,
                           error=error.message if error else ""), (409 if isinstance(error, CostingConflict) else
                                                                  422 if error else 200)


@material_prices.get("")
@permission_required("manage_costs")
def page():
    return _form(MaterialPriceService(service()), grade=request.args.get("grade", "SPHC"),
                 price=request.args.get("price", ""))


@material_prices.post("/preview")
@permission_required("manage_costs")
def preview():
    catalog = MaterialPriceService(service())
    grade, price = request.form.get("grade", ""), request.form.get("price", "")
    try:
        change = catalog.request(grade, price)
        result = catalog.preview(change)
    except (CostingValidationError, CostingConflict) as error:
        return _form(catalog, grade=grade, price=price, error=error)
    token = _signer().dumps([import_owner(), change, result["digest"]])
    if wants_json_response():
        return jsonify(ok=True, preview=result, token=token)
    return render_template("costing_material_prices.html", preview=result, grade=grade, price=price, token=token)


@material_prices.post("/apply")
@permission_required("manage_costs")
def apply():
    catalog = MaterialPriceService(service())
    try:
        owner, change, digest = _signer().loads(request.form.get("token", ""), max_age=30 * 60)
    except BadData:
        return _form(catalog, error=CostingValidationError("调价预览已过期或无效，请重新预览。"))
    try:
        if owner != import_owner():
            raise CostingValidationError("请用当前账号重新预览调价。")
        result = catalog.apply(change, digest, actor=actor_name())
    except (CostingValidationError, CostingConflict) as error:
        return _form(catalog, grade=change["grade"], price=change["price"], error=error)
    if wants_json_response():
        return jsonify(ok=True, models=result["updated_models"], rows=result["updated_rows"])
    flash(f"已调整 {result['grade']} 单价，更新 {result['updated_models']} 个型号、{result['updated_rows']} 行明细。", "success")
    return redirect(url_for("costing_material_prices.page", grade=change["grade"]))
