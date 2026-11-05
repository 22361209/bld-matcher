"""Translate the flat development sheet form into one application-service write."""
from __future__ import annotations

import json
import logging

from flask import abort, flash, redirect, render_template, request, url_for

from app.security import actor_name

from .domain import DevelopmentValidationError, fail
from .factory import get_development_service
from .worksheet import LEGACY_MATERIALS, MAX_WORKSHEET_ROWS, WORKSHEET_STATUSES, worksheet_status


logger = logging.getLogger(__name__)
MAX_ROWS_JSON_CHARS = 256_000


def render_sheet(sheet, *, project_id=None, expected_version=None, editable=True, notes=None, errors=(), status=200):
    save_url = (url_for("development_update_sheet", project_id=project_id) if project_id
                else url_for("development_create_sheet"))
    return render_template("development_sheet.html", sheet=sheet, save_url=save_url, can_edit=editable,
                           worksheet_status=worksheet_status(sheet), status_choices=WORKSHEET_STATUSES,
                           body_material_options=LEGACY_MATERIALS, notes=notes or "", errors=errors,
                           expected_version=expected_version, max_rows=MAX_WORKSHEET_ROWS,
                           max_rows_chars=MAX_ROWS_JSON_CHARS), status


def save_sheet(project_id=None):
    # Forms already open before the third state was introduced still post 0/1.
    status = request.form.get("status")
    if status is None:
        status = {"0": "not_started", "1": "in_progress"}.get(request.form.get("started", "0"), "")
    sheet = {"core_code": request.form.get("core_code", ""), "started": status in ("in_progress", "completed")}
    if status == "completed":
        sheet["completed"] = True
    expected_version = request.form.get("expected_version", "")
    notes = request.form.get("notes")
    try:
        raw = request.form.get("rows", "")
        if len(raw) > MAX_ROWS_JSON_CHARS:
            fail("明细过长，请减少行数或缩短填写内容。")
        try:
            rows = json.loads(raw)
        except (ValueError, RecursionError):
            fail("零件明细未能读取，请刷新页面后重试。")
        if not isinstance(rows, list) or any(not isinstance(row, dict) or
                                             any(not isinstance(value, str) for value in row.values()) for row in rows):
            fail("请使用表内的加减号编辑零件明细。")
        # An old open tab may have inserted one row past the limit. Keep it editable
        # so the user can choose what to remove, but bound the size of the rendered form.
        if len(rows) > MAX_WORKSHEET_ROWS + 1:
            fail(f"明细超过 {MAX_WORKSHEET_ROWS} 行，请缩减行数。")
        sheet["rows"] = rows
        if status not in WORKSHEET_STATUSES:
            fail("请选择有效的开发状态。")
        service = get_development_service()
        if project_id:
            detail = service.update_worksheet(project_id, sheet, actor=actor_name(), expected_version=expected_version, notes=notes)
        else:
            detail = service.create_worksheet(sheet, actor=actor_name(), notes=notes)
    except DevelopmentValidationError as exc:
        if exc.code == "development.not_found":
            abort(404)
        if "rows" not in sheet:
            return render_template("development_sheet.html", recovery_error=exc.message, project_id=project_id), 400
        status = 409 if exc.code == "development.version_conflict" else 400
        return render_sheet(sheet, project_id=project_id, expected_version=expected_version, notes=notes,
                            errors=[exc.message], status=status)
    except Exception:
        logger.exception("Development worksheet save failed", extra={"project_id": project_id})
        return render_sheet(sheet, project_id=project_id, expected_version=expected_version, notes=notes,
                            errors=["保存失败，填写内容已保留。"], status=500)
    flash("新品开发表已保存。", "success")
    return redirect(url_for("development_detail", project_id=detail["project"]["id"]))
