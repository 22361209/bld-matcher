from __future__ import annotations

import logging
from math import ceil
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.helpers import user_upload_path
from app.security import actor_name, permission_required, safe_referrer

from .domain import QuoteValidationError
from .factory import get_quote_service
from .service import QuoteImportBusyError, QuoteImportError, QuoteNotFoundError, QuoteVersionConflictError


logger = logging.getLogger(__name__)
quote_web = Blueprint("quote_web", __name__)
QUOTE_PAGE_SIZE = 100


def _filters() -> dict[str, str]:
    return {
        "customer_name": request.args.get("customer_name", request.args.get("customer", "")).strip(),
        "bld_no": request.args.get(
            "bld_no",
            request.args.get("product_model", request.args.get("model", "")),
        ).strip(),
        "date_from": request.args.get("date_from", "").strip(),
        "date_to": request.args.get("date_to", "").strip(),
        "currency": request.args.get("currency", "").strip().upper(),
        "quoted_by": request.args.get("quoted_by", "").strip(),
    }


def _request_page() -> int:
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _page_url(filters: dict[str, str], page: int) -> str:
    params = {key: value for key, value in filters.items() if value}
    if page > 1:
        params["page"] = str(page)
    return f"{url_for('quote_web.quotes', **params)}#quote-results"


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


@quote_web.get("/customer-prices")
@permission_required("view_customer_prices")
def old_customer_prices_redirect():
    return redirect(url_for("quote_web.quotes"))


@quote_web.get("/quotes", endpoint="quotes")
@permission_required("view_customer_prices")
def quotes():
    filters = _filters()
    requested_page = _request_page()
    try:
        service = get_quote_service()
        first_page = service.list_records(filters, limit=QUOTE_PAGE_SIZE, offset=0)
        pagination = _pagination(filters, requested_page, first_page.total)
        page_number = int(pagination["page"])
        page = (
            first_page
            if page_number == 1
            else service.list_records(
                filters,
                limit=QUOTE_PAGE_SIZE,
                offset=(page_number - 1) * QUOTE_PAGE_SIZE,
            )
        )
        latest = None
        if filters["customer_name"] and filters["bld_no"]:
            latest = service.latest(
                customer_name=filters["customer_name"],
                bld_no=filters["bld_no"],
            )
        stats = service.stats().as_dict()
        records = page.records
        total = page.total
    except QuoteValidationError as exc:
        flash(exc.message, "error")
        records = []
        latest = None
        stats = {"total": 0, "customers": 0, "models": 0}
        pagination = _pagination(filters, 1, 0)
        total = 0
    except Exception:
        logger.exception("Quote page query failed")
        flash("查询失败，请稍后重试。", "error")
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


@quote_web.post("/quotes/save", endpoint="save_quote")
@permission_required("manage_customer_prices")
def save_quote():
    try:
        get_quote_service().create(dict(request.form), actor=actor_name())
    except QuoteValidationError as exc:
        flash(f"保存失败：{exc.message}", "error")
        return redirect(url_for("quote_web.quotes"))
    except Exception:
        logger.exception("Quote save failed")
        flash("保存失败，请稍后重试。", "error")
        return redirect(url_for("quote_web.quotes"))
    flash("报价记录已保存。", "success")
    return redirect(
        url_for(
            "quote_web.quotes",
            customer_name=request.form.get("customer_name", ""),
            bld_no=request.form.get("bld_no", ""),
        )
        + "#quote-results"
    )


@quote_web.post("/quotes/<int:quote_id>/edit", endpoint="edit_quote")
@permission_required("manage_customer_prices")
def edit_quote(quote_id: int):
    version_text = request.form.get("version", "").strip()
    expected_version = int(version_text) if version_text.isdigit() else None
    try:
        get_quote_service().update(
            quote_id,
            dict(request.form),
            actor=actor_name(),
            expected_version=expected_version,
        )
    except QuoteValidationError as exc:
        flash(f"修正失败：{exc.message}", "error")
        return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))
    except QuoteNotFoundError:
        flash("报价记录不存在。", "error")
        return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))
    except QuoteVersionConflictError:
        flash("报价记录已被其他操作修改，请刷新页面后重试。", "error")
        return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))
    except Exception:
        logger.exception("Quote update failed")
        flash("修正失败，请稍后重试。", "error")
        return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))
    flash("报价记录已修正，并保留修改日志。", "success")
    return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))


@quote_web.post("/quotes/import/preview", endpoint="quote_import_preview")
@permission_required("manage_customer_prices")
def quote_import_preview():
    file = request.files.get("quote_file")
    customer_name = request.form.get("customer_name", "").strip()
    currency = request.form.get("currency", "").strip().upper()
    if not file or not file.filename:
        flash("请选择报价记录 Excel 文件。", "error")
        return redirect(url_for("quote_web.quotes"))
    if Path(file.filename).suffix.lower() not in {".xls", ".xlsx"}:
        flash("报价记录导入文件支持 .xls 和 .xlsx。", "error")
        return redirect(url_for("quote_web.quotes"))

    upload_path = user_upload_path(file.filename, prefix="quote-records")
    file.save(upload_path)
    service = get_quote_service()
    try:
        preview = service.preview_import(
            upload_path,
            customer_name=customer_name,
            currency=currency,
        )
    except QuoteValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("quote_web.quotes"))
    except QuoteImportError as exc:
        flash(f"解析失败：{exc}", "error")
        return redirect(url_for("quote_web.quotes"))
    return render_template(
        "quote_import.html",
        preview=preview,
        payload=service.encode_import_rows(preview["rows"]),
    )


@quote_web.post("/quotes/import/apply", endpoint="quote_import_apply")
@permission_required("manage_customer_prices")
def quote_import_apply():
    try:
        imported, skipped = get_quote_service().apply_import_payload(
            request.form.get("payload", "[]"),
            actor=actor_name(),
        )
    except QuoteImportBusyError as exc:
        flash(str(exc), "error")
        return redirect(url_for("quote_web.quotes"))
    except (QuoteImportError, QuoteValidationError) as exc:
        flash(f"导入数据无效：{exc}", "error")
        return redirect(url_for("quote_web.quotes"))
    except Exception:
        logger.exception("Quote import failed")
        flash("导入失败，请稍后重试。", "error")
        return redirect(url_for("quote_web.quotes"))
    flash(f"报价记录导入完成：新增 {imported} 条，跳过 {skipped} 条。", "success")
    return redirect(url_for("quote_web.quotes"))


def register(app) -> None:
    app.register_blueprint(quote_web)
