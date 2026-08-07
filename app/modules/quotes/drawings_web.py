from __future__ import annotations

import logging

from flask import flash, redirect, request, url_for

from app.security import actor_name, permission_required, safe_referrer

from .domain import QuoteValidationError
from .factory import get_quote_service
from .service import QuoteNotFoundError
from .web import quote_web


logger = logging.getLogger(__name__)


def _quote_detail_return_url(record=None) -> str:
    quote_no = record.quote_no if record is not None else ""
    fallback = url_for("quote_web.quotes", quote_no=quote_no) if quote_no else url_for("quote_web.quotes")
    return safe_referrer(fallback + "#quote-results")


@quote_web.post("/quotes/<int:quote_id>/drawings/link", endpoint="link_quote_drawing")
@permission_required("edit_customer_prices")
def link_quote_drawing(quote_id: int):
    record = None
    try:
        record = get_quote_service().link_drawing(
            quote_id,
            request.form.get("drawing_file_id", ""),
            actor=actor_name(),
        )
    except QuoteValidationError as exc:
        flash(f"关联图纸失败：{exc.message}", "error")
    except QuoteNotFoundError:
        flash("报价记录不存在。", "error")
    except Exception:
        logger.exception("Quote drawing link failed")
        flash("关联图纸失败，请稍后重试。", "error")
    else:
        flash("报价行已关联图纸版本。", "success")
    return redirect(_quote_detail_return_url(record))


@quote_web.post("/quotes/<int:quote_id>/drawings/<int:link_id>/unlink", endpoint="unlink_quote_drawing")
@permission_required("edit_customer_prices")
def unlink_quote_drawing(quote_id: int, link_id: int):
    record = None
    try:
        record = get_quote_service().unlink_drawing(quote_id, link_id, actor=actor_name())
    except QuoteValidationError as exc:
        flash(f"解除图纸关联失败：{exc.message}", "error")
    except QuoteNotFoundError:
        flash("报价记录不存在。", "error")
    except Exception:
        logger.exception("Quote drawing unlink failed")
        flash("解除图纸关联失败，请稍后重试。", "error")
    else:
        flash("已解除报价行的图纸关联。", "success")
    return redirect(_quote_detail_return_url(record))
