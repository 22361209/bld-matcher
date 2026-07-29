from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from flask import Blueprint, flash, make_response, redirect, render_template, request, send_file, url_for

from app.config import OUTPUT_DIR
from app.helpers import user_upload_path
from app.security import actor_name, permission_required, safe_referrer

from .domain import QuoteValidationError
from .factory import get_quote_service
from .list_query import filters_from_request, page_url, pagination as build_pagination, requested_page
from .service import QuoteImportBusyError, QuoteImportError, QuoteNotFoundError, QuoteVersionConflictError


logger = logging.getLogger(__name__)
quote_web = Blueprint("quote_web", __name__)
QUOTE_PAGE_SIZE = 100
WEB_EDITABLE_QUOTE_FIELDS = (
    "customer_name",
    "bld_no",
    "customer_product_code",
    "tax_price",
    "net_price",
    "currency",
    "quote_date",
    "remark",
)


def _quote_attachment_path(reference: str) -> Path | None:
    candidate = PurePosixPath(reference)
    if not reference or candidate.is_absolute() or ".." in candidate.parts:
        return None
    path = (OUTPUT_DIR / candidate.as_posix()).resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in path.parents or not path.is_file():
        return None
    return path


def _web_quote_data() -> dict[str, str]:
    return {field: request.form.get(field, "") for field in WEB_EDITABLE_QUOTE_FIELDS if field in request.form}


@quote_web.get("/customer-prices")
@permission_required("view_customer_prices")
def old_customer_prices_redirect():
    return redirect(url_for("quote_web.quotes"))


@quote_web.get("/quotes", endpoint="quotes")
@permission_required("view_customer_prices")
def quotes():
    return render_template("quotes.html", **_quote_list_context())


def _quote_list_context() -> dict[str, object]:
    filters = filters_from_request()
    page = requested_page()
    try:
        service = get_quote_service()
        first_page = service.list_records(filters, limit=QUOTE_PAGE_SIZE, offset=0)
        pagination = build_pagination(filters, page, first_page.total, page_size=QUOTE_PAGE_SIZE)
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
        quote_filter_options = service.filter_options(filters)
        quote_column_filters = dict(filters.get("column_filters", {}))
        records = page.records
        total = page.total
    except QuoteValidationError as exc:
        flash(exc.message, "error")
        records = []
        latest = None
        stats = {"total": 0, "customers": 0, "models": 0}
        pagination = build_pagination(filters, 1, 0, page_size=QUOTE_PAGE_SIZE)
        total = 0
        quote_filter_options = {}
        quote_column_filters = {}
    except Exception:
        logger.exception("Quote page query failed")
        flash("查询失败，请稍后重试。", "error")
        records = []
        latest = None
        stats = {"total": 0, "customers": 0, "models": 0}
        pagination = build_pagination(filters, 1, 0, page_size=QUOTE_PAGE_SIZE)
        total = 0
        quote_filter_options = {}
        quote_column_filters = {}
    return {
        "records": records,
        "latest": latest,
        "filters": filters,
        "quote_filter_options": quote_filter_options,
        "quote_column_filters": quote_column_filters,
        "total_records": total,
        "stats": stats,
        "pagination": pagination,
        "page_size": QUOTE_PAGE_SIZE,
        "canonical_url": page_url(filters, int(pagination["page"])).split("#", 1)[0],
    }


@quote_web.get("/quotes/fragment", endpoint="quotes_fragment")
@permission_required("view_customer_prices")
def quotes_fragment():
    response = make_response(render_template("_quote_results.html", **_quote_list_context()))
    response.headers["Cache-Control"] = "no-store"
    return response


@quote_web.get("/quotes/number/<quote_no>", endpoint="quote_number_detail")
@permission_required("view_customer_prices")
def quote_number_detail(quote_no: str):
    service = get_quote_service()
    records = service.records_by_quote_no(quote_no)
    contract_documents = service.contract_documents_by_quote_no(quote_no)
    attachments = []
    seen_paths: set[str] = set()
    for record in records:
        if record.attachment_path in seen_paths or not _quote_attachment_path(record.attachment_path):
            continue
        seen_paths.add(record.attachment_path)
        attachments.append(
            {
                "name": Path(record.attachment_path).name,
                "url": url_for("quote_web.download_quote_attachment", quote_no=quote_no, quote_id=record.id),
            }
        )
    response = make_response(
        render_template(
            "_quote_number_detail.html",
            quote_no=quote_no,
            records=records,
            attachments=attachments,
            contract_documents=contract_documents,
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@quote_web.get("/quotes/number/<quote_no>/attachment/<int:quote_id>", endpoint="download_quote_attachment")
@permission_required("view_customer_prices")
def download_quote_attachment(quote_no: str, quote_id: int):
    record = next(
        (item for item in get_quote_service().records_by_quote_no(quote_no) if item.id == quote_id),
        None,
    )
    path = _quote_attachment_path(record.attachment_path) if record else None
    if path is None:
        flash("报价文件不存在或无权访问。", "error")
        return redirect(url_for("quote_web.quotes", quote_no=quote_no) + "#quote-results")
    return send_file(path, as_attachment=True, download_name=path.name)


@quote_web.post("/quotes/save", endpoint="save_quote")
@permission_required("manage_customer_prices")
def save_quote():
    actor = actor_name()
    values = _web_quote_data()
    values.update({"quoted_by": actor, "source_type": "manual"})
    try:
        get_quote_service().create(values, actor=actor)
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
            _web_quote_data(),
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


@quote_web.post("/quotes/<int:quote_id>/delete", endpoint="delete_quote")
@permission_required("manage_customer_prices")
def delete_quote(quote_id: int):
    try:
        get_quote_service().delete(quote_id, actor=actor_name())
    except QuoteNotFoundError:
        flash("报价记录不存在。", "error")
        return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))
    except Exception:
        logger.exception("Quote delete failed")
        flash("删除失败，请稍后重试。", "error")
        return redirect(safe_referrer(url_for("quote_web.quotes") + "#quote-results"))
    flash("报价记录已删除。", "success")
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
