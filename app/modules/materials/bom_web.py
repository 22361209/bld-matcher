from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.security import actor_name, permission_required
from app.modules.tubes.factory import get_tube_service

from .bom_store import ExistingBomLink, KINDS, TUBE_KINDS
from .factory import get_material_service


def register(app):
    @app.get("/materials/items")
    @permission_required("manage_material_items")
    def material_items():
        service = get_material_service()
        args = request.args
        parent = args.get("parent", "")
        selected = args.get("selected", "")
        mode = args.get("mode", "")
        view = args.get("view", "tree")
        status = args.get("status", "active")
        context = service.bom_page(args.get("q", ""), status, args.get("page", 1, type=int) or 1, view, selected)
        tube = None
        if context["detail"] and context["detail"]["kind"] in TUBE_KINDS:
            tube = get_tube_service().bom_detail(selected)
        tubes = get_tube_service().bom_catalog() if mode == "attach" else []
        return render_template("material_bom.html", **context, parent=parent, mode=mode, kinds=KINDS,
                               tube=tube, tubes=tubes, tube_kinds=TUBE_KINDS)

    @app.post("/materials/bom/save")
    @permission_required("manage_material_items")
    def save_material_bom():
        data = request.form
        try:
            get_material_service().change_bom(data, actor_name())
        except ExistingBomLink as exc:
            flash(str(exc), "error")
            return redirect(url_for("material_items", mode="usage", parent=data.get("parent"), selected=data.get("code")))
        except ValueError as exc:
            flash(str(exc), "error")
            if data.get("action") == "merge":
                return redirect(url_for("material_items", view="review"))
            return redirect(url_for("material_items", mode=data.get("action"), parent=data.get("parent"),
                                    selected=data.get("source") or data.get("code")))
        flash("生产 BOM 已保存。", "success")
        if not data.get("parent") and data.get("action") in ("create", "copy", "edit"):
            return redirect(url_for("material_items", view="library", mode="detail", selected=data.get("code")))
        return redirect(url_for("material_items", q=data.get("parent") or data.get("code")))
