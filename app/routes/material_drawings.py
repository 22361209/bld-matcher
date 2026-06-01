from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import abort, flash, redirect, render_template, request, send_file, url_for

from app.config import DATA_DIR, DB_PATH
from app.database import connect, log_event
from app.helpers import clean_original_filename, safe_upload_name
from app.security import actor_name, login_required, permission_required


MATERIAL_DRAWING_DIR = DATA_DIR / "material_drawings"
ALLOWED_DRAWING_SUFFIXES = {".pdf"}


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


def _list_drawings(query: str) -> list[dict[str, object]]:
    MATERIAL_DRAWING_DIR.mkdir(parents=True, exist_ok=True)
    query_text = query.strip().lower()
    drawings = []
    for path in sorted(MATERIAL_DRAWING_DIR.glob("*.pdf"), key=lambda item: item.name.lower()):
        if query_text and query_text not in path.name.lower():
            continue
        stat = path.stat()
        drawings.append(
            {
                "name": path.name,
                "size_kb": max(1, round(stat.st_size / 1024)),
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return drawings


def register(app) -> None:
    @app.get("/material-drawings")
    @login_required
    def material_drawings():
        query = request.args.get("q", "")
        return render_template(
            "material_drawings.html",
            drawings=_list_drawings(query),
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

    @app.get("/material-drawings/<path:name>")
    @login_required
    def download_material_drawing(name: str):
        path = _drawing_path(name)
        if not path.exists() or path.suffix.lower() not in ALLOWED_DRAWING_SUFFIXES:
            abort(404)
        return send_file(path, as_attachment=True, download_name=path.name)
