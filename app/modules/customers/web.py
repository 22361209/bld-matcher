from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.security import actor_name, login_required, permission_required, wants_json_response

from .domain import CustomerValidationError
from .factory import get_customer_service


logger = logging.getLogger(__name__)


def register(app) -> None:
    @app.get("/customers")
    @permission_required("manage_customers")
    def customers():
        service = get_customer_service()
        return render_template("customers.html", customers=service.list())

    @app.get("/customers/lookup")
    @login_required
    def customer_lookup():
        query = request.args.get("q", "")
        limit_text = request.args.get("limit", "").strip()
        limit = int(limit_text) if limit_text.isdigit() else 20
        matches = get_customer_service().lookup(query, limit=max(1, min(50, limit)))
        response = jsonify([{"id": customer.id, "name": customer.name} for customer in matches])
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/customers/save")
    @permission_required("manage_customers")
    def save_customer():
        name = request.form.get("name", "")
        id_text = request.form.get("id", "").strip()
        customer_id = int(id_text) if id_text.isdigit() else None
        try:
            service = get_customer_service()
            customer = service.rename(customer_id, name, actor=actor_name()) if customer_id else service.create(name, actor=actor_name())
        except CustomerValidationError as exc:
            if wants_json_response():
                return jsonify({"ok": False, "error": exc.message}), 400
            flash(f"客户保存失败：{exc.message}", "error")
            return redirect(url_for("customers"))
        except Exception:
            logger.exception("Customer save failed")
            if wants_json_response():
                return jsonify({"ok": False, "error": "客户保存失败，请稍后重试。"}), 500
            flash("客户保存失败，请稍后重试。", "error")
            return redirect(url_for("customers"))
        if wants_json_response():
            return jsonify({"ok": True, "customer": {"id": customer.id, "name": customer.name}})
        flash("客户已保存。", "success")
        return redirect(url_for("customers"))

    @app.post("/customers/delete")
    @permission_required("manage_customers")
    def delete_customer():
        id_text = request.form.get("id", "").strip()
        customer_id = int(id_text) if id_text.isdigit() else None
        try:
            if customer_id is None:
                raise CustomerValidationError("customer.id_required", "缺少客户编号。")
            get_customer_service().delete(customer_id, actor=actor_name())
        except CustomerValidationError as exc:
            flash(f"客户删除失败：{exc.message}", "error")
            return redirect(url_for("customers"))
        except Exception:
            logger.exception("Customer delete failed")
            flash("客户删除失败，请稍后重试。", "error")
            return redirect(url_for("customers"))
        flash("客户已删除。", "success")
        return redirect(url_for("customers"))
