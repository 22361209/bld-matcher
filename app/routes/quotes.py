from __future__ import annotations

from math import ceil
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.config import DB_PATH
from app.database import (
    connect,
    count_quote_records,
    create_quote_record,
    get_quote_record,
    latest_quote_record,
    list_quote_records,
    quote_record_payload,
    quote_record_stats,
    update_quote_record,
)
from app.helpers import user_upload_path
from app.locks import ImportLockError, import_lock
from app.platform.api_auth import api_actor_name, internal_api_required
from app.quote_import import decode_rows, encode_rows, parse_quote_import_file
from app.security import actor_name, permission_required, safe_referrer


QUOTE_PAGE_SIZE = 100


def _payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return dict(request.form)


def _json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _filters() -> dict[str, str]:
    return {
        "customer_name": request.args.get("customer_name", request.args.get("customer", "")).strip(),
        "bld_no": request.args.get("bld_no", request.args.get("product_model", request.args.get("model", ""))).strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "currency": request.args.get("currency", "").strip().upper(),
        "quoted_by": request.args.get("quoted_by", "").strip(),
    }


def _request_limit(default: int = QUOTE_PAGE_SIZE) -> int:
    try:
        return max(1, min(500, int(request.args.get("limit", default))))
    except ValueError:
        return default


def _request_offset() -> int:
    try:
        return max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return 0


def _request_page() -> int:
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _page_url(filters: dict[str, str], page: int) -> str:
    params = {key: value for key, value in filters.items() if value}
    if page > 1:
        params["page"] = str(page)
    return f"{url_for('quotes', **params)}#quote-results"


def _pagination(filters: dict[str, str], page: int, total: int) -> dict[str, object]:
    total_pages = max(1, ceil(total / QUOTE_PAGE_SIZE))
    page = min(max(1, page), total_pages)
    window = {1, total_pages, page - 1, page, page + 1}
    links = []
    previous_page = 0
    for item in sorted(value for value in window if 1 <= value <= total_pages):
        if previous_page and item - previous_page > 1:
            links.append({"gap": True})
        links.append({"page": item, "url": _page_url(filters, item), "current": item == page})
        previous_page = item
    return {
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": _page_url(filters, page - 1) if page > 1 else "",
        "next_url": _page_url(filters, page + 1) if page < total_pages else "",
        "links": links,
    }


def _api_actor() -> str:
    return api_actor_name()


def register(app) -> None:
    @app.get("/customer-prices")
    @permission_required("view_customer_prices")
    def old_customer_prices_redirect():
        return redirect(url_for("quotes"))

    @app.get("/quotes")
    @permission_required("view_customer_prices")
    def quotes():
        filters = _filters()
        page = _request_page()
        try:
            with connect(DB_PATH) as conn:
                total = count_quote_records(conn, **filters)
                pagination = _pagination(filters, page, total)
                records = list_quote_records(
                    conn,
                    **filters,
                    limit=QUOTE_PAGE_SIZE,
                    offset=(int(pagination["page"]) - 1) * QUOTE_PAGE_SIZE,
                )
                latest = None
                if filters["customer_name"] and filters["bld_no"]:
                    latest = latest_quote_record(
                        conn,
                        customer_name=filters["customer_name"],
                        bld_no=filters["bld_no"],
                    )
                stats = quote_record_stats(conn)
        except Exception as exc:
            flash(f"查询失败：{exc}", "error")
            records = []
            latest = None
            stats = {"total": 0, "customers": 0, "models": 0}
            pagination = _pagination(filters, 1, 0)
            total = 0
        return render_template(
            "quotes.html",
            records=records,
            latest=latest,
            filters=filters,
            total_records=total,
            stats=stats,
            pagination=pagination,
            page_size=QUOTE_PAGE_SIZE,
        )

    @app.post("/quotes/save")
    @permission_required("manage_customer_prices")
    def save_quote():
        try:
            with connect(DB_PATH) as conn:
                create_quote_record(conn, request.form, actor=actor_name())
        except Exception as exc:
            flash(f"保存失败：{exc}", "error")
            return redirect(url_for("quotes"))
        flash("报价记录已保存。", "success")
        return redirect(url_for("quotes", customer_name=request.form.get("customer_name", ""), bld_no=request.form.get("bld_no", "")) + "#quote-results")

    @app.post("/quotes/<int:quote_id>/edit")
    @permission_required("manage_customer_prices")
    def edit_quote(quote_id: int):
        try:
            with connect(DB_PATH) as conn:
                row = update_quote_record(conn, quote_id, request.form, actor=actor_name())
        except Exception as exc:
            flash(f"修正失败：{exc}", "error")
            return redirect(safe_referrer(url_for("quotes") + "#quote-results"))
        if not row:
            flash("报价记录不存在。", "error")
        else:
            flash("报价记录已修正，并保留修改日志。", "success")
        return redirect(safe_referrer(url_for("quotes") + "#quote-results"))

    @app.post("/quotes/import/preview")
    @permission_required("manage_customer_prices")
    def quote_import_preview():
        file = request.files.get("quote_file")
        customer_name = request.form.get("customer_name", "").strip()
        currency = request.form.get("currency", "").strip().upper()
        if not customer_name:
            flash("请填写客户名称。", "error")
            return redirect(url_for("quotes"))
        if currency not in {"CNY", "USD", "EUR"}:
            flash("请选择币种。", "error")
            return redirect(url_for("quotes"))

        if not file or not file.filename:
            flash("请选择报价记录 Excel 文件。", "error")
            return redirect(url_for("quotes"))
        if Path(file.filename).suffix.lower() not in {".xls", ".xlsx"}:
            flash("报价记录导入文件支持 .xls 和 .xlsx。", "error")
            return redirect(url_for("quotes"))

        upload_path = user_upload_path(file.filename, prefix="quote-records")
        file.save(upload_path)
        try:
            preview = parse_quote_import_file(upload_path, customer_name=customer_name, currency=currency)
        except Exception as exc:
            flash(f"解析失败：{exc}", "error")
            return redirect(url_for("quotes"))
        return render_template("quote_import.html", preview=preview, payload=encode_rows(preview["rows"]))

    @app.post("/quotes/import/apply")
    @permission_required("manage_customer_prices")
    def quote_import_apply():
        try:
            rows = decode_rows(request.form.get("payload", "[]"))
        except Exception as exc:
            flash(f"导入数据无效：{exc}", "error")
            return redirect(url_for("quotes"))

        try:
            with import_lock(actor_name(), "报价记录批量导入"):
                imported = 0
                skipped = 0
                with connect(DB_PATH) as conn:
                    for row in rows:
                        if row.get("status") != "valid":
                            skipped += 1
                            continue
                        create_quote_record(conn, row, actor=actor_name(), commit=False)
                        imported += 1
                    conn.commit()
        except ImportLockError as exc:
            flash(str(exc), "error")
            return redirect(url_for("quotes"))
        except Exception as exc:
            flash(f"导入失败：{exc}", "error")
            return redirect(url_for("quotes"))
        flash(f"报价记录导入完成：新增 {imported} 条，跳过 {skipped} 条。", "success")
        return redirect(url_for("quotes"))

    @app.post("/api/quotes")
    @internal_api_required
    def api_create_quote():
        try:
            with connect(DB_PATH) as conn:
                quote_id = create_quote_record(conn, _payload(), actor=_api_actor())
                row = get_quote_record(conn, quote_id)
        except Exception as exc:
            return _json_error(str(exc))
        return jsonify({"ok": True, "quote": quote_record_payload(row)}), 201

    @app.get("/api/quotes")
    @internal_api_required
    def api_list_quotes():
        filters = _filters()
        try:
            with connect(DB_PATH) as conn:
                rows = list_quote_records(conn, **filters, limit=_request_limit(), offset=_request_offset())
        except Exception as exc:
            return _json_error(str(exc))
        return jsonify({"ok": True, "quotes": [quote_record_payload(row) for row in rows]})

    @app.get("/api/quotes/latest")
    @internal_api_required
    def api_latest_quote():
        try:
            with connect(DB_PATH) as conn:
                row = latest_quote_record(
                    conn,
                    customer_name=request.args.get("customer_name", ""),
                    bld_no=request.args.get("bld_no", request.args.get("product_model", "")),
                )
        except Exception as exc:
            return _json_error(str(exc))
        if not row:
            return jsonify({"ok": True, "quote": None})
        return jsonify({"ok": True, "quote": quote_record_payload(row)})

    @app.put("/api/quotes/<int:quote_id>")
    @internal_api_required
    def api_update_quote(quote_id: int):
        try:
            with connect(DB_PATH) as conn:
                row = update_quote_record(conn, quote_id, _payload(), actor=_api_actor())
        except Exception as exc:
            return _json_error(str(exc))
        if not row:
            return _json_error("报价记录不存在。", 404)
        return jsonify({"ok": True, "quote": quote_record_payload(row)})
