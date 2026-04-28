from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import flash, redirect, render_template, request, send_file, url_for

from app.catalog_export import export_products_xlsx
from app.config import CATALOG_PATH, DATA_DIR, DB_PATH, OUTPUT_DIR, UPLOAD_DIR
from app.database import (
    bootstrap_from_excel,
    connect,
    deactivate_product,
    get_product,
    import_catalog,
    list_products,
    log_event,
    product_stats,
    upsert_product,
)
from app.helpers import safe_upload_name
from app.price_import import decode_rows, encode_rows, parse_price_file
from app.security import actor_name, login_required, permission_required


def register(app) -> None:
    @app.post("/catalog")
    @permission_required("import_catalog")
    def upload_catalog():
        redirect_target = url_for("products") if request.form.get("next") == "products" else url_for("index")
        file = request.files.get("catalog")
        if not file or not file.filename:
            flash("请选择产品目录 Excel 文件。", "error")
            return redirect(redirect_target)
        if not file.filename.lower().endswith(".xlsx"):
            flash("产品目录请使用 .xlsx 文件。", "error")
            return redirect(redirect_target)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        backup = DATA_DIR / f"catalog-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
        if CATALOG_PATH.exists():
            shutil.copy2(CATALOG_PATH, backup)
        file.save(CATALOG_PATH)
        try:
            with connect(DB_PATH) as conn:
                import_catalog(conn, CATALOG_PATH, replace=False, actor=actor_name())
        except Exception as exc:
            if backup.exists():
                shutil.copy2(backup, CATALOG_PATH)
            flash(f"目录读取失败，已恢复旧目录：{exc}", "error")
            return redirect(redirect_target)

        flash("产品目录已导入。已有 BLD NO. 会更新，新增 BLD NO. 会加入产品库。", "success")
        return redirect(redirect_target)

    @app.get("/products")
    @login_required
    def products():
        query = request.args.get("q", "")
        bld_query = request.args.get("bld", "")
        oe_query = request.args.get("oe", "")
        if oe_query.strip():
            bld_query = ""
        status = request.args.get("status", "active")
        if status not in {"active", "all", "inactive"}:
            status = "active"
        with connect(DB_PATH) as conn:
            bootstrap_from_excel(DB_PATH, CATALOG_PATH)
            rows = list_products(
                conn,
                query=query,
                bld_query=bld_query,
                oe_query=oe_query,
                include_inactive=status == "all",
                only_inactive=status == "inactive",
                limit=3000,
            )
            stats = product_stats(conn)
        return render_template(
            "products.html",
            products=rows,
            query=query,
            bld_query=bld_query or query,
            oe_query=oe_query,
            status=status,
            stats=stats,
        )

    @app.get("/products/export")
    @login_required
    def export_products_options():
        status = request.args.get("status", "all")
        return render_template("export_catalog.html", status=status)

    @app.post("/products/export")
    @login_required
    def export_products():
        status = request.form.get("status", "all")
        include_inactive = status != "active"
        export_format = request.form.get("export_format", "bld")
        if export_format not in {"bld", "brand"}:
            export_format = "bld"
        format_label = "brand" if export_format == "brand" else "bld"
        output_path = OUTPUT_DIR / f"catalog-export-{format_label}-{datetime.now().strftime('%y%m%d')}.xlsx"
        counter = 2
        while output_path.exists():
            output_path = OUTPUT_DIR / f"catalog-export-{format_label}-{datetime.now().strftime('%y%m%d')}_{counter}.xlsx"
            counter += 1
        with connect(DB_PATH) as conn:
            export_products_xlsx(conn, output_path, include_inactive=include_inactive, export_format=export_format)
            log_event(
                conn,
                "导出目录",
                "catalog",
                output_path.name,
                ("按汽车品牌格式；" if export_format == "brand" else "按 BLD 号格式；")
                + ("包含停用产品" if include_inactive else "仅启用产品"),
                actor=actor_name(),
            )
            conn.commit()
        return send_file(output_path, as_attachment=True)

    @app.get("/prices/import")
    @permission_required("edit_products")
    def price_import():
        return render_template("price_import.html", preview=None)

    @app.post("/prices/import/preview")
    @permission_required("edit_products")
    def price_import_preview():
        file = request.files.get("price_file")
        if not file or not file.filename:
            flash("请选择单价 Excel 文件。", "error")
            return redirect(url_for("price_import"))
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".xls", ".xlsx"}:
            flash("单价导入文件支持 .xls 和 .xlsx。", "error")
            return redirect(url_for("price_import"))

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload_path = UPLOAD_DIR / f"price-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_upload_name(file.filename)}"
        file.save(upload_path)
        try:
            with connect(DB_PATH) as conn:
                preview = parse_price_file(upload_path, conn)
        except Exception as exc:
            flash(f"解析失败：{exc}", "error")
            return redirect(url_for("price_import"))
        return render_template("price_import.html", preview=preview, payload=encode_rows(preview["rows"]))

    @app.post("/prices/import/apply")
    @permission_required("edit_products")
    def price_import_apply():
        try:
            rows = decode_rows(request.form.get("payload", "[]"))
        except Exception as exc:
            flash(f"导入数据无效：{exc}", "error")
            return redirect(url_for("price_import"))

        updated = 0
        skipped = 0
        with connect(DB_PATH) as conn:
            for row in rows:
                if row.get("status") != "matched":
                    skipped += 1
                    continue
                conn.execute(
                    "UPDATE products SET price_cny = ?, updated_at = ? WHERE bld_no = ?",
                    (row["price"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["bld_no"]),
                )
                updated += 1
            log_event(conn, "批量维护单价", "product", "Unit Price", f"更新 {updated} 条，跳过 {skipped} 条", actor=actor_name())
            conn.commit()
        flash(f"单价导入完成：更新 {updated} 条，跳过 {skipped} 条。", "success")
        return redirect(url_for("products"))

    @app.get("/products/new")
    @permission_required("edit_products")
    def new_product():
        return render_template("product_form.html", product=None)

    @app.get("/products/<int:product_id>/edit")
    @permission_required("edit_products")
    def edit_product(product_id: int):
        with connect(DB_PATH) as conn:
            product = get_product(conn, product_id)
        if not product:
            flash("产品不存在。", "error")
            return redirect(url_for("products"))
        return render_template("product_form.html", product=product)

    @app.post("/products/save")
    @permission_required("edit_products")
    def save_product():
        data = {
            "bld_no": request.form.get("bld_no", ""),
            "series": request.form.get("series", ""),
            "item": request.form.get("item", ""),
            "oe_no_1": request.form.get("oe_no_1", ""),
            "oe_no_2": request.form.get("oe_no_2", ""),
            "models": request.form.get("models", ""),
            "price_cny": request.form.get("price_cny", ""),
            "image_path": request.form.get("image_path", ""),
            "active": request.form.get("active", "0"),
        }
        try:
            with connect(DB_PATH) as conn:
                upsert_product(conn, data, source="web", actor=actor_name())
        except Exception as exc:
            flash(f"保存失败：{exc}", "error")
            return redirect(url_for("products"))
        flash("产品已保存。", "success")
        return redirect(url_for("products", q=data["bld_no"]))

    @app.post("/products/<int:product_id>/deactivate")
    @permission_required("edit_products")
    def stop_product(product_id: int):
        with connect(DB_PATH) as conn:
            deactivate_product(conn, product_id, actor=actor_name())
        flash("产品已停用，历史资料仍保留。", "success")
        return redirect(url_for("products"))
