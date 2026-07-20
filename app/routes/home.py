from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for

from app.config import CATALOG_PATH, OUTPUT_DIR
from app.helpers import all_recent_outputs, download_name, user_output_dir, user_recent_outputs
from app.matcher import catalog_summary
from app.modules.inquiry.factory import get_inquiry_service
from app.modules.products.factory import get_product_service
from app.security import can, login_required


QUICK_FILTER_LABELS = {
    "bld": "只看BLD号",
    "oe": "只看OE号",
    "brand": "只看品牌号",
}


def _is_inquiry_result(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() not in {".xls", ".xlsx"}:
        return False
    return "catalog-export" not in name and "料单" not in path.name


def _operation_user(path: Path) -> str:
    parent = path.parent.name
    if not parent.startswith("u") or "-" not in parent:
        return "历史文件"
    return parent.split("-", 1)[1] or parent


def _history_rows(paths: list[Path], query: str) -> list[dict]:
    needle = query.strip().lower()
    rows = []
    for path in paths:
        if not _is_inquiry_result(path):
            continue
        operator = _operation_user(path)
        if needle and needle not in path.name.lower() and needle not in operator.lower():
            continue
        stat = path.stat()
        rows.append(
            {
                "path": path,
                "name": path.name,
                "operator": operator,
                "kind": path.suffix.lower().lstrip(".").upper(),
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
    return rows[:80]


def _load_history_rows(query: str) -> list[dict]:
    output_candidates = (
        all_recent_outputs(limit=500) if can("manage_users") else user_recent_outputs(limit=500)
    )
    return _history_rows(output_candidates, query)


def _history_payload(query: str) -> dict:
    rows = _load_history_rows(query)
    return {
        "count": len(rows),
        "rows": [
            {
                "name": row["name"],
                "kind": row["kind"],
                "operator": row["operator"],
                "updated_at": row["updated_at"],
                "download_url": url_for("download", name=download_name(row["path"])),
            }
            for row in rows
        ],
    }


def register(app) -> None:
    @app.get("/")
    @login_required
    def index():
        history_query = request.args.get("history_q", "").strip()
        quick_oe = request.args.get("quick_oe", "").strip()
        quick_filter = request.args.get("quick_filter", "").strip()
        if quick_filter not in QUICK_FILTER_LABELS:
            quick_filter = ""
        product_service = get_product_service()
        catalog = product_service.catalog()
        stats = product_service.stats().as_dict()
        history_files = _load_history_rows(history_query) if history_query else []
        quick_results = (
            get_inquiry_service().quick_search(quick_oe, catalog=catalog)
            if can("generate_match") and quick_oe and catalog is not None
            else []
        )
        return render_template(
            "index.html",
            catalog_summary=catalog_summary(catalog) if catalog else None,
            product_stats=stats,
            catalog_path=CATALOG_PATH if CATALOG_PATH.exists() else None,
            quick_oe=quick_oe,
            quick_filter=quick_filter,
            quick_filter_labels=QUICK_FILTER_LABELS,
            quick_results=quick_results,
            history_query=history_query,
            history_files=history_files,
            history_loaded=bool(history_query),
        )

    @app.get("/history-files")
    @login_required
    def inquiry_history_files():
        query = request.args.get("history_q", "").strip()
        return jsonify(_history_payload(query))

    @app.get("/download/<path:name>")
    @login_required
    def download(name: str):
        candidates = []
        if "/" not in name:
            if can("manage_users"):
                candidates.append(OUTPUT_DIR / name)
            candidates.append(user_output_dir(create=False) / name)
        candidates.append(OUTPUT_DIR / name)
        path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        if not path or OUTPUT_DIR.resolve() not in path.parents:
            flash("文件不存在。", "error")
            return redirect(url_for("index"))
        if not can("manage_users"):
            user_root = user_output_dir(create=False).resolve()
            if user_root not in path.parents:
                flash("当前账号没有权限下载这个文件。", "error")
                return redirect(url_for("index"))
        return send_file(path, as_attachment=True)
