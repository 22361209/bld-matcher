from __future__ import annotations

import logging
from pathlib import Path

from flask import flash, redirect, render_template, request, url_for

from app.helpers import download_name, user_output_dir, user_recent_outputs, user_upload_dir, user_upload_path
from app.security import actor_name, permission_required

from .factory import get_shipping_notice_service
from .infrastructure import ALLOWED_SHIPMENT_SUFFIXES


logger = logging.getLogger(__name__)


def _page_context(*, selected_template_id: str = "", generate_preview=None) -> dict[str, object]:
    return get_shipping_notice_service().page_context(
        selected_template_id=selected_template_id,
        generate_preview=generate_preview,
        recent_outputs=user_recent_outputs("发货通知/*.xlsx", limit=20),
    )


def register(app) -> None:
    @app.get("/shipping-notices")
    @permission_required("generate_shipping_notice")
    def shipping_notice():
        return render_template(
            "shipping_notice.html",
            **_page_context(selected_template_id=request.args.get("template_id", "")),
        )

    @app.post("/shipping-notices/templates/upload")
    @permission_required("generate_shipping_notice")
    def upload_shipping_notice_template():
        customer = request.form.get("customer", "").strip()
        name = request.form.get("template_name", "").strip()
        file = request.files.get("template")
        if not customer or not name or not file or not file.filename:
            flash("请填写客户、模板名称，并选择单个模板文件。", "error")
            return redirect(url_for("shipping_notice"))
        try:
            template = get_shipping_notice_service().upload_template(
                file,
                customer=customer,
                name=name,
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"模板上传失败：{exc}", "error")
            return redirect(url_for("shipping_notice"))
        except Exception:
            logger.exception("Shipping template upload failed")
            flash("模板上传失败，请稍后重试。", "error")
            return redirect(url_for("shipping_notice"))
        flash("模板已上传。", "success")
        return redirect(url_for("shipping_notice", template_id=template["id"]))

    @app.post("/shipping-notices/templates/batch")
    @permission_required("generate_shipping_notice")
    def batch_upload_shipping_notice_templates():
        file = request.files.get("template_zip")
        if not file or not file.filename:
            flash("请选择模板压缩包。", "error")
            return redirect(url_for("shipping_notice"))
        if Path(file.filename).suffix.lower() != ".zip":
            flash("批量模板请上传 .zip 文件。", "error")
            return redirect(url_for("shipping_notice"))
        upload_path = user_upload_path(file.filename, prefix="shipping-template-batch")
        file.save(upload_path)
        try:
            imported, errors = get_shipping_notice_service().batch_upload(upload_path, actor=actor_name())
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("shipping_notice"))
        except Exception:
            logger.exception("Shipping template batch upload failed")
            flash("模板批量导入失败，请稍后重试。", "error")
            return redirect(url_for("shipping_notice"))
        if errors:
            flash(f"已导入 {imported} 个模板，{len(errors)} 个失败。首个失败：{errors[0]}", "error")
        else:
            flash(f"已导入 {imported} 个模板。", "success")
        return redirect(url_for("shipping_notice"))

    @app.post("/shipping-notices/preview")
    @permission_required("generate_shipping_notice")
    def preview_shipping_notice():
        template_id = request.form.get("template_id", "").strip()
        file = request.files.get("shipment_data")
        if not file or not file.filename:
            flash("请选择发货数据文件。", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        if Path(file.filename).suffix.lower() not in ALLOWED_SHIPMENT_SUFFIXES:
            flash("发货数据仅支持 .xlsx 或 .csv。", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        upload_path = user_upload_path(file.filename, prefix="shipping-data")
        file.save(upload_path)
        try:
            preview = get_shipping_notice_service().preview_shipment(
                template_id=template_id,
                upload_path=upload_path,
            )
        except ValueError as exc:
            flash(f"发货数据读取失败：{exc}", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        except Exception:
            logger.exception("Shipping data preview failed")
            flash("发货数据读取失败，请稍后重试。", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        return render_template(
            "shipping_notice_preview.html",
            **_page_context(selected_template_id=template_id, generate_preview=preview),
        )

    @app.post("/shipping-notices/generate")
    @permission_required("generate_shipping_notice")
    def generate_shipping_notice():
        template_id = request.form.get("template_id", "").strip()
        upload_path = Path(request.form.get("upload_path", "")).expanduser().resolve()
        upload_root = user_upload_dir(create=False).resolve()
        if not upload_path.is_file() or upload_root not in upload_path.parents:
            flash("发货数据路径无效，请重新上传预览。", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        try:
            output_path = get_shipping_notice_service().generate(
                template_id=template_id,
                upload_path=upload_path,
                output_dir=user_output_dir() / "发货通知",
                actor=actor_name(),
            )
        except ValueError as exc:
            flash(f"发货通知生成失败：{exc}", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        except Exception:
            logger.exception("Shipping notice generation failed")
            flash("发货通知生成失败，请稍后重试。", "error")
            return redirect(url_for("shipping_notice", template_id=template_id))
        flash("发货通知 Excel 已生成。", "success")
        return redirect(
            url_for(
                "shipping_notice",
                template_id=template_id,
                generated=download_name(output_path),
            )
        )
