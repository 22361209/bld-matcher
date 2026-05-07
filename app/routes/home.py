from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from flask import flash, redirect, render_template, request, send_file, url_for

from app.config import CATALOG_PATH, DB_PATH, OUTPUT_DIR
from app.database import connect, product_stats
from app.helpers import all_recent_outputs, load_catalog, user_output_dir, user_recent_outputs
from app.matcher import CatalogMatch, ProductCatalog, catalog_summary, compact_text, normalize_code, split_codes
from app.security import can
from app.security import login_required


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


def _product_from_match(match: CatalogMatch) -> dict:
    row = match.row
    return {
        "bld_no": match.bld_no,
        "series": row.get("SERIES", ""),
        "item": row.get("ITEM", ""),
        "oe_no_1": row.get("OE NO.1", ""),
        "oe_no_2": row.get("OE NO.2", ""),
        "models": row.get("Models", ""),
        "price_cny": row.get("price_cny"),
        "image_path": row.get("image_path", ""),
        "image_path_2": row.get("image_path_2", ""),
        "image_path_3": row.get("image_path_3", ""),
        "image_path_4": row.get("image_path_4", ""),
        "image_path_5": row.get("image_path_5", ""),
    }


def _quick_result_from_match(query: str, match: CatalogMatch) -> dict:
    return {
        "query": query,
        "product": _product_from_match(match),
        "reason": match.reason,
        "score": match.score,
    }


def _quick_bld_fragment_results(catalog: ProductCatalog, query: str) -> list[dict]:
    key = normalize_code(query)
    if not re.fullmatch(r"\d{4}", key):
        return []

    results = []
    seen = set()
    for row in catalog.rows:
        bld_no = compact_text(row.get("BLD NO."))
        bld_key = normalize_code(bld_no)
        if not bld_no or key not in bld_key or bld_key in seen:
            continue
        seen.add(bld_key)
        match = CatalogMatch(bld_no, 86, "BLD NO. 片段命中", row, matched_codes=(query,))
        results.append(_quick_result_from_match(query, match))
    return results


def _quick_oe_results(catalog: ProductCatalog | None, query: str) -> list[dict]:
    if not catalog:
        return []

    codes = split_codes(query)
    if not codes and query.strip():
        codes = [query.strip()]

    results = []
    for code in codes[:20]:
        bld_fragment_results = _quick_bld_fragment_results(catalog, code)
        if bld_fragment_results:
            results.extend(bld_fragment_results)
            continue

        match = catalog.match("", code)
        if match:
            results.append(_quick_result_from_match(code, match))
        else:
            results.append({"query": code, "product": None, "reason": "未找到", "score": 0})
    return results


def register(app) -> None:
    @app.get("/")
    @login_required
    def index():
        history_query = request.args.get("history_q", "").strip()
        quick_oe = request.args.get("quick_oe", "").strip()
        catalog = load_catalog()
        with connect(DB_PATH) as conn:
            stats = product_stats(conn)
        output_candidates = all_recent_outputs(limit=500) if can("manage_users") else user_recent_outputs(limit=500)
        history_files = _history_rows(output_candidates, history_query)
        quick_results = _quick_oe_results(catalog, quick_oe) if can("generate_match") and quick_oe else []
        return render_template(
            "index.html",
            catalog_summary=catalog_summary(catalog) if catalog else None,
            product_stats=stats,
            catalog_path=CATALOG_PATH if CATALOG_PATH.exists() else None,
            quick_oe=quick_oe,
            quick_results=quick_results,
            history_query=history_query,
            history_files=history_files,
        )

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
