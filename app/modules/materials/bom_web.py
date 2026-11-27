from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from app.security import actor_name, permission_required
from app.modules.tubes.factory import get_tube_service

from .bom_common import normalize_plate_code
from .bom_store import ExistingBomLink, KINDS, TUBE_KINDS
from .factory import get_material_service


def pagination(view, query, status, page, pages, total, filter_query=None):
    def page_url(number):
        return url_for("material_items", view=view, q=query, status=status, page=number, **(filter_query or {}))

    numbers = sorted({1, pages, page - 1, page, page + 1})
    links = []
    previous = 0
    for number in numbers:
        if not 1 <= number <= pages:
            continue
        if previous and number - previous > 1:
            links.append({"gap": True})
        links.append({"page": number, "url": page_url(number), "current": number == page})
        previous = number
    return {"page": page, "total_pages": pages, "has_prev": page > 1, "has_next": page < pages,
            "prev_url": page_url(page - 1), "next_url": page_url(page + 1), "jump_url": page_url(1),
            "start": (page - 1) * 30 + 1 if total else 0, "end": min(total, page * 30), "links": links}


def register(app):
    @app.get("/materials/items")
    @permission_required("manage_material_items")
    def material_items():
        service = get_material_service()
        args = request.args
        parent = args.get("parent", "")
        selected = normalize_plate_code(args.get("selected", ""))
        mode = args.get("mode", "")
        view = args.get("view", "tree")
        status = args.get("status", "active")
        if view not in ("tree", "library", "review"):
            view = "tree"
        if status not in ("active", "all", "inactive", "removed"):
            status = "active"
        if mode not in ("", "assembly", "attach", "usage", "create", "copy", "detail", "edit"):
            mode = "detail"
        filter_query = {f"cf_{key}": args.getlist(f"cf_{key}") for key in
                        ("code", "name", "model", "kind", "grade", "spec", "pieces", "gross_weight", "net_weight", "reference_count", "actions")}
        filter_query = {key: values for key, values in filter_query.items() if values}
        context = service.bom_page(args.get("q", ""), status, args.get("page", 1, type=int) or 1, view, selected, filter_query)
        tube = None
        if context["detail"] and context["detail"]["kind"] in TUBE_KINDS:
            tube = get_tube_service().bom_detail(selected)
        tubes = get_tube_service().bom_catalog() if mode in ("assembly", "attach", "create", "copy") else []
        page_links = pagination(view, context["q"], status, context["page"], context["pages"], context["total"], filter_query)
        return render_template("material_bom.html", **context, parent=parent, mode=mode, kinds=KINDS,
                               tube=tube, tubes=tubes, tube_kinds=TUBE_KINDS, pagination=page_links,
                               filter_query=filter_query)

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
            action = data.get("action") or ""
            if action == "merge":
                return redirect(url_for("material_items", view="review"))
            if action == "remove":
                return redirect(url_for("material_items", mode="usage", parent=data.get("parent"), selected=data.get("code")))
            if action == "restore":
                return redirect(url_for("material_items", view="tree", status="removed"))
            if action == "delete":
                return redirect(url_for("material_items", view="library", mode="detail", selected=data.get("code")))
            return redirect(url_for("material_items", mode=data.get("mode") or action, parent=data.get("parent"),
                                    selected=data.get("source") or data.get("code"), view=data.get("source_view", "tree")))
        action = data.get("action") or ""
        flash({"remove": "装配关系已移除，可在“已移除”中恢复。", "restore": "装配关系已恢复。",
               "delete": "材料明细已删除。"}.get(action, "生产 BOM 已保存。"), "success")
        if action == "restore":
            return redirect(url_for("material_items", view="tree", status="removed", q=data.get("parent")))
        if action == "delete":
            return redirect(url_for("material_items", view="library"))
        if action == "assembly_save" or (action == "create" and data.get("mode") == "assembly"):
            flash("母件及子件清单已保存。", "success")
            return redirect(url_for("material_items", view="tree", mode="detail", selected=data.get("code")))
        if not data.get("parent") and action in ("create", "copy", "edit"):
            return redirect(url_for("material_items", view="library", mode="detail", selected=data.get("code")))
        return redirect(url_for("material_items", q=data.get("parent") or data.get("code")))
