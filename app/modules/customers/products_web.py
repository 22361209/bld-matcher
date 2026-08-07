from __future__ import annotations

import logging

from flask import abort, flash, jsonify, redirect, request, url_for

from app.modules.customer_products.domain import CustomerProductValidationError
from app.modules.customer_products.factory import get_customer_product_service
from app.security import actor_name, permission_required, wants_json_response


logger = logging.getLogger(__name__)

DRAWING_KINDS = frozenset({"bld", "customer"})


def _detail_url(customer_id: int) -> str:
    return url_for("customer_detail", customer_id=customer_id, view="products")


def _failed(action: str, customer_id: int, exc: Exception):
    if isinstance(exc, CustomerProductValidationError):
        message = f"{action}失败：{exc.message}"
        status = 400
    else:
        logger.exception(
            "Customer product operation failed",
            extra={"action": action, "customer_id": customer_id},
        )
        message = f"{action}失败，请稍后重试。"
        status = 500
    if wants_json_response():
        return jsonify({"ok": False, "error": message}), status
    flash(message, "error")
    return redirect(_detail_url(customer_id))


def register(app) -> None:
    @app.post("/customers/<int:customer_id>/products")
    @permission_required("edit_customers")
    def create_customer_product(customer_id: int):
        drawing_files = tuple(
            upload
            for upload in request.files.getlist("customer_drawing_file")
            if str(getattr(upload, "filename", "") or "").strip()
        )
        try:
            get_customer_product_service().create(
                customer_id,
                request.form.get("bld_no", ""),
                request.form.get("customer_product_code", ""),
                request.form.get("customer_product_name", ""),
                customer_drawing_files=drawing_files,
                customer_drawing_revision_label=request.form.get("customer_drawing_revision_label", ""),
                actor=actor_name(),
            )
        except Exception as exc:
            return _failed("新增客户商品", customer_id, exc)
        if wants_json_response():
            return jsonify({"ok": True})
        flash("客户商品已新增。", "success")
        return redirect(_detail_url(customer_id))

    @app.post("/customers/<int:customer_id>/products/<int:product_id>/update")
    @permission_required("edit_customers")
    def update_customer_product(customer_id: int, product_id: int):
        try:
            get_customer_product_service().update(
                customer_id,
                product_id,
                request.form.get("customer_product_code", ""),
                request.form.get("customer_product_name", ""),
                actor=actor_name(),
            )
        except Exception as exc:
            return _failed("保存客户商品", customer_id, exc)
        if wants_json_response():
            return jsonify({"ok": True})
        flash("客户商品已保存。", "success")
        return redirect(_detail_url(customer_id))

    @app.post("/customers/<int:customer_id>/products/<int:product_id>/delete")
    @permission_required("delete_customers")
    def delete_customer_product(customer_id: int, product_id: int):
        try:
            product = get_customer_product_service().delete(
                customer_id,
                product_id,
                actor=actor_name(),
            )
        except Exception as exc:
            return _failed("删除客户商品", customer_id, exc)
        drawing_count = sum(len(slot.files) for slot in product.drawings)
        if wants_json_response():
            return jsonify({"ok": True, "deleted_drawing_count": drawing_count})
        suffix = f"，并永久删除 {drawing_count} 个图纸版本文件" if drawing_count else ""
        flash(f"客户商品 {product.bld_no} 已删除{suffix}。", "success")
        return redirect(_detail_url(customer_id))

    @app.post("/customers/<int:customer_id>/products/<int:product_id>/drawings/<kind>/versions")
    @permission_required("edit_customers")
    def upload_customer_product_drawing_version(customer_id: int, product_id: int, kind: str):
        if kind not in DRAWING_KINDS:
            abort(404)
        try:
            product = get_customer_product_service().upload_version(
                customer_id,
                product_id,
                kind,
                request.files.getlist("files"),
                revision_label=request.form.get("revision_label", ""),
                note=request.form.get("note", ""),
                actor=actor_name(),
            )
        except Exception as exc:
            return _failed("上传图纸版本", customer_id, exc)
        slot = product.slot(kind)
        version_no = slot.current_version if slot else 0
        if wants_json_response():
            return jsonify({"ok": True, "version_no": version_no})
        flash(f"图纸 V{version_no} 已上传并设为当前版本。", "success")
        return redirect(_detail_url(customer_id))

    @app.post("/customers/<int:customer_id>/products/<int:product_id>/drawings/<kind>/current")
    @permission_required("edit_customers")
    def set_customer_product_drawing_current(customer_id: int, product_id: int, kind: str):
        if kind not in DRAWING_KINDS:
            abort(404)
        version_text = request.form.get("version_no", "").strip()
        try:
            get_customer_product_service().set_current_version(
                customer_id,
                product_id,
                kind,
                version_text,
                actor=actor_name(),
            )
        except Exception as exc:
            return _failed("设置当前图纸版本", customer_id, exc)
        if wants_json_response():
            return jsonify({"ok": True, "version_no": int(version_text)})
        flash(f"已切换为图纸 V{version_text}。", "success")
        return redirect(_detail_url(customer_id))

    @app.post("/customers/<int:customer_id>/products/<int:product_id>/drawings/bld/import-catalog")
    @permission_required("edit_customers")
    def import_customer_product_catalog_drawing(customer_id: int, product_id: int):
        try:
            product = get_customer_product_service().import_catalog_drawing(
                customer_id,
                product_id,
                actor=actor_name(),
            )
        except Exception as exc:
            return _failed("引入产品目录图纸", customer_id, exc)
        slot = product.slot("bld")
        version_no = slot.current_version if slot else 0
        if wants_json_response():
            return jsonify({"ok": True, "version_no": version_no})
        flash(f"产品目录图纸已引入为 BLD 图纸 V{version_no}。", "success")
        return redirect(_detail_url(customer_id))

    @app.get("/customers/<int:customer_id>/products/<int:product_id>/drawings/<kind>/versions.json")
    @permission_required("view_customers")
    def customer_product_drawing_versions(customer_id: int, product_id: int, kind: str):
        if kind not in DRAWING_KINDS:
            abort(404)
        service = get_customer_product_service()
        try:
            product = next(
                (item for item in service.list_for_customer(customer_id) if item.id == product_id),
                None,
            )
            if product is None:
                raise CustomerProductValidationError("customer_product.not_found", "客户商品不存在。")
        except Exception as exc:
            if isinstance(exc, CustomerProductValidationError):
                return jsonify({"ok": False, "error": exc.message}), 404
            logger.exception(
                "Customer product drawing versions query failed",
                extra={"customer_id": customer_id, "product_id": product_id},
            )
            return jsonify({"ok": False, "error": "图纸版本读取失败，请稍后重试。"}), 500
        slot = product.slot(kind)
        kind_labels = {entry["value"]: entry["label"] for entry in service.kinds()}
        versions = []
        if slot is not None:
            for version in slot.versions:
                versions.append(
                    {
                        "version_no": version.version_no,
                        "revision_label": version.revision_label,
                        "note": version.note,
                        "created_at": version.file.created_at,
                        "file_id": version.file.id,
                        "original_name": version.file.original_name,
                        "content_type": version.file.content_type,
                        "previewable": version.file.previewable,
                        "is_current": version.version_no == slot.current_version,
                        "preview_url": url_for(
                            "preview_customer_drawing_file",
                            customer_id=customer_id,
                            file_id=version.file.id,
                        ),
                        "download_url": url_for(
                            "download_customer_drawing_file",
                            customer_id=customer_id,
                            file_id=version.file.id,
                        ),
                    }
                )
        response = jsonify(
            {
                "ok": True,
                "kind": kind,
                "kind_label": slot.kind_label if slot is not None else kind_labels[kind],
                "bld_no": product.bld_no,
                "current_version": slot.current_version if slot is not None else 0,
                "catalog_has_drawing": bool(product.catalog and product.catalog.has_drawing),
                "versions": versions,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response
