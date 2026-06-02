from __future__ import annotations

from datetime import datetime
from pathlib import Path

import re

from flask import abort, flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR, DB_PATH
from app.database import connect, log_event
from app.helpers import clean_original_filename, safe_upload_name
from app.security import actor_name, login_required, permission_required


MATERIAL_DRAWING_DIR = DATA_DIR / "material_drawings"
ALLOWED_DRAWING_SUFFIXES = {".pdf"}
DEFAULT_DRAWING_CATEGORY = "球销"


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _drawing_path(name: str) -> Path:
    filename = Path(name or "").name
    path = (MATERIAL_DRAWING_DIR / filename).resolve()
    root = MATERIAL_DRAWING_DIR.resolve()
    if root != path.parent:
        abort(404)
    return path


def _unique_drawing_path(filename: str) -> Path:
    safe_name = safe_upload_name(clean_original_filename(filename, fallback_suffix=".pdf"))
    candidate = MATERIAL_DRAWING_DIR / safe_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return MATERIAL_DRAWING_DIR / f"{stem}-{timestamp}{suffix}"


def _drawing_code(path: Path) -> str:
    return path.stem.strip()


def _drawing_category(path: Path) -> str:
    return DEFAULT_DRAWING_CATEGORY


def _drawing_record(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "code": _drawing_code(path),
        "category": _drawing_category(path),
        "name": path.name,
        "size_kb": max(1, round(stat.st_size / 1024)),
        "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


def _all_drawing_records() -> list[dict[str, object]]:
    MATERIAL_DRAWING_DIR.mkdir(parents=True, exist_ok=True)
    return [
        _drawing_record(path)
        for path in sorted(MATERIAL_DRAWING_DIR.glob("*.pdf"), key=lambda item: _natural_key(item.stem))
    ]


def _category_options(records: list[dict[str, object]]) -> list[str]:
    return sorted({str(record["category"]) for record in records}, key=_natural_key)


def _list_drawings(query: str, category: str) -> list[dict[str, object]]:
    records = _all_drawing_records()
    query_text = query.strip().lower()
    drawings: list[dict[str, object]] = []
    for record in records:
        code = str(record["code"])
        record_category = str(record["category"])
        name = str(record["name"])
        if category and category != record_category:
            continue
        if query_text and not any(query_text in value.lower() for value in (code, record_category, name)):
            continue
        drawings.append(record)
    return drawings


def register(app) -> None:
    @app.get("/material-drawings")
    @login_required
    def material_drawings():
        query = request.args.get("q", "")
        category = request.args.get("category", "")
        selected_name = Path(request.args.get("selected", "")).name
        records = _all_drawing_records()
        categories = _category_options(records)
        if category not in categories:
            category = ""
        drawings = _list_drawings(query, category)
        selected = next((drawing for drawing in drawings if drawing["name"] == selected_name), None)
        if not selected and drawings:
            selected = drawings[0]
        return render_template(
            "material_drawings.html",
            drawings=drawings,
            selected_drawing=selected,
            total_drawings=len(records),
            categories=categories,
            category=category,
            query=query,
        )

    @app.post("/material-drawings/upload")
    @permission_required("manage_materials")
    def upload_material_drawing():
        file = request.files.get("drawing")
        if not file or not file.filename:
            flash("请选择物料图纸 PDF 文件。", "error")
            return redirect(url_for("material_drawings"))
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_DRAWING_SUFFIXES:
            flash("物料图纸目前仅支持 PDF 文件。", "error")
            return redirect(url_for("material_drawings"))

        MATERIAL_DRAWING_DIR.mkdir(parents=True, exist_ok=True)
        destination = _unique_drawing_path(file.filename)
        file.save(destination)
        with connect(DB_PATH) as conn:
            log_event(
                conn,
                "上传物料图纸",
                "material_drawing",
                destination.name,
                f"上传物料图纸 {destination.name}",
                actor=actor_name(),
            )
            conn.commit()
        flash("物料图纸已上传。", "success")
        return redirect(url_for("material_drawings"))

    @app.get("/material-drawings/preview/<path:name>")
    @login_required
    def preview_material_drawing(name: str):
        path = _drawing_path(name)
        if not path.exists() or path.suffix.lower() not in ALLOWED_DRAWING_SUFFIXES:
            abort(404)
        return send_file(path, as_attachment=False, mimetype="application/pdf", download_name=path.name)

    @app.get("/material-drawings/<path:name>")
    @login_required
    def download_material_drawing(name: str):
        path = _drawing_path(name)
        if not path.exists() or path.suffix.lower() not in ALLOWED_DRAWING_SUFFIXES:
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name)
