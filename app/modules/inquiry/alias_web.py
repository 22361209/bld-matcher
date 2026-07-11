from __future__ import annotations

from flask import flash, redirect, request, url_for

from app.matcher import normalize_code
from app.modules.inquiry.factory import get_inquiry_service
from app.security import actor_name, permission_required


def register(app) -> None:
    @app.post("/manual-map")
    @permission_required("manage_aliases")
    def add_manual_map():
        source_code = request.form.get("source_code", "")
        bld_no = request.form.get("bld_no", "")
        if not normalize_code(source_code) or not normalize_code(bld_no):
            flash("请输入客户号码和 BLD NO.。", "error")
            return redirect(url_for("index"))

        sync_target = request.form.get("sync_target", "oe")
        appended = get_inquiry_service().save_alias(
            source_code,
            bld_no,
            request.form.get("note", ""),
            sync_target,
            actor=actor_name(),
        )
        target_label = "OE 号" if request.form.get("sync_target", "oe") == "oe" else "品牌号码"
        flash("人工映射已保存。" + (f" 已同步加入产品目录{target_label}。" if appended else ""), "success")
        return redirect(url_for("index"))

    @app.post("/manual-map/delete")
    @permission_required("manage_aliases")
    def delete_manual_map():
        source_code = request.form.get("source_code", "")
        get_inquiry_service().delete_alias(source_code, actor=actor_name())
        flash("人工映射已删除。", "success")
        return redirect(url_for("index"))
