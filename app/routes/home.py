from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for

from app.config import CATALOG_PATH, DB_PATH, OUTPUT_DIR
from app.database import connect, product_stats
from app.helpers import all_recent_outputs, download_name, load_catalog, user_output_dir, user_recent_outputs
from app.matcher import (
    CatalogMatch,
    ProductCatalog,
    brand_code_aliases,
    catalog_summary,
    compact_text,
    normalize_code,
    psa_352x_key,
    split_codes,
)
from app.security import can
from app.security import login_required


QUICK_SEARCH_MIN_LENGTH = 4
QUICK_SEARCH_LIMIT = 80
QUICK_FILTER_LABELS = {
    "bld": "只看BLD号",
    "oe": "只看OE号",
    "brand": "只看品牌号",
}


@dataclass(frozen=True)
class QuickCandidate:
    match: CatalogMatch
    match_type: str
    hit_code: str
    hit_label: str


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
    output_candidates = all_recent_outputs(limit=500) if can("manage_users") else user_recent_outputs(limit=500)
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


def _quick_result_from_match(query: str, candidate: QuickCandidate) -> dict:
    match = candidate.match
    return {
        "query": query,
        "product": _product_from_match(match),
        "reason": match.reason,
        "score": match.score,
        "match_type": candidate.match_type,
        "hit_code": candidate.hit_code,
        "hit_label": candidate.hit_label,
    }


def _quick_match_type(reason: str) -> str:
    if "BLD" in reason:
        return "bld"
    if "品牌号码" in reason:
        return "brand"
    return "oe"


def _add_quick_candidate(
    candidates: dict[str, QuickCandidate],
    *,
    query: str,
    row: dict,
    score: int,
    reason: str,
    match_type: str,
    hit_code: str,
    hit_label: str,
) -> None:
    bld_no = compact_text(row.get("BLD NO."))
    bld_key = normalize_code(bld_no)
    if not bld_key:
        return

    existing = candidates.get(bld_key)
    if existing and existing.match.score >= score:
        return
    candidates[bld_key] = QuickCandidate(
        CatalogMatch(bld_no, score, reason, row, matched_codes=(query,)),
        match_type,
        compact_text(hit_code) or compact_text(query),
        hit_label,
    )


def _candidate_from_match(match: CatalogMatch) -> QuickCandidate:
    match_type = _quick_match_type(match.reason)
    label = {"bld": "BLD号", "brand": "品牌号"}.get(match_type, "OE号")
    hit_code = match.matched_codes[0] if match.matched_codes else match.bld_no
    return QuickCandidate(match, match_type, compact_text(hit_code), label)


def _quick_candidate_matches(catalog: ProductCatalog, query: str) -> list[QuickCandidate]:
    key = normalize_code(query)
    if len(key) < QUICK_SEARCH_MIN_LENGTH:
        return []

    psa_probe = psa_352x_key(query)
    if psa_probe:
        psa_match = catalog.match("", query)
        if psa_match and ("PSA" in psa_match.reason or "3520/3521" in psa_match.reason):
            return [_candidate_from_match(psa_match)]
        if psa_probe[1]:
            return []

    candidates: dict[str, QuickCandidate] = {}
    for row in catalog.rows:
        bld_no = compact_text(row.get("BLD NO."))
        bld_key = normalize_code(bld_no)
        if key and bld_key:
            if key == bld_key:
                _add_quick_candidate(candidates, query=query, row=row, score=96, reason="BLD NO. 精准命中", match_type="bld", hit_code=bld_no, hit_label="BLD号")
            elif key in bld_key:
                _add_quick_candidate(candidates, query=query, row=row, score=86, reason="BLD NO. 片段命中", match_type="bld", hit_code=bld_no, hit_label="BLD号")

        for field in ("OE NO.1", "OE NO.2"):
            match_type = "brand" if field == "OE NO.2" else "oe"
            hit_label = "品牌号" if field == "OE NO.2" else "OE号"
            exact_reason = "品牌号码精准命中" if field == "OE NO.2" else "OE 精准命中"
            prefix_reason = "品牌号码前缀命中" if field == "OE NO.2" else "OE 前缀命中"
            partial_reason = "品牌号码片段命中" if field == "OE NO.2" else "OE 片段命中"
            for code in split_codes(row.get(field)):
                aliases = brand_code_aliases(code) if field == "OE NO.2" else [code]
                for alias in aliases:
                    code_key = normalize_code(alias)
                    if not code_key:
                        continue
                    if key == code_key:
                        _add_quick_candidate(candidates, query=query, row=row, score=95, reason=exact_reason, match_type=match_type, hit_code=code, hit_label=hit_label)
                    elif code_key.startswith(key):
                        _add_quick_candidate(candidates, query=query, row=row, score=90, reason=prefix_reason, match_type=match_type, hit_code=code, hit_label=hit_label)
                    elif key in code_key:
                        _add_quick_candidate(candidates, query=query, row=row, score=82, reason=partial_reason, match_type=match_type, hit_code=code, hit_label=hit_label)

    for source_key, manual_bld in catalog.manual_map.items():
        row = catalog.by_bld.get(normalize_code(manual_bld))
        if not row:
            continue
        if key == source_key:
            _add_quick_candidate(candidates, query=query, row=row, score=100, reason="人工映射号码精准命中", match_type="oe", hit_code=source_key, hit_label="人工映射")
        elif source_key.startswith(key):
            _add_quick_candidate(candidates, query=query, row=row, score=90, reason="人工映射号码前缀命中", match_type="oe", hit_code=source_key, hit_label="人工映射")
        elif key in source_key:
            _add_quick_candidate(candidates, query=query, row=row, score=82, reason="人工映射号码片段命中", match_type="oe", hit_code=source_key, hit_label="人工映射")

    if not candidates:
        match = catalog.match("", query)
        if match:
            fallback = _candidate_from_match(match)
            _add_quick_candidate(
                candidates,
                query=query,
                row=match.row,
                score=match.score,
                reason=match.reason,
                match_type=fallback.match_type,
                hit_code=fallback.hit_code,
                hit_label=fallback.hit_label,
            )

    return sorted(candidates.values(), key=lambda candidate: (-candidate.match.score, normalize_code(candidate.match.bld_no)))[:QUICK_SEARCH_LIMIT]


def _quick_oe_results(catalog: ProductCatalog | None, query: str) -> list[dict]:
    if not catalog:
        return []

    codes = split_codes(query)
    if not codes and query.strip():
        codes = [query.strip()]

    results = []
    for code in codes[:20]:
        key = normalize_code(code)
        if len(key) < QUICK_SEARCH_MIN_LENGTH:
            results.append({"query": code, "product": None, "reason": "请输入至少 4 位号码", "score": 0})
            continue

        matches = _quick_candidate_matches(catalog, code)
        if matches:
            for candidate in matches:
                results.append(_quick_result_from_match(code, candidate))
        else:
            results.append({"query": code, "product": None, "reason": "未找到", "score": 0})

        if len(results) >= QUICK_SEARCH_LIMIT:
            break

    return results[:QUICK_SEARCH_LIMIT]


def register(app) -> None:
    @app.get("/")
    @login_required
    def index():
        history_query = request.args.get("history_q", "").strip()
        quick_oe = request.args.get("quick_oe", "").strip()
        quick_filter = request.args.get("quick_filter", "").strip()
        if quick_filter not in QUICK_FILTER_LABELS:
            quick_filter = ""
        catalog = load_catalog()
        with connect(DB_PATH) as conn:
            stats = product_stats(conn)
        history_files = _load_history_rows(history_query) if history_query else []
        quick_results = _quick_oe_results(catalog, quick_oe) if can("generate_match") and quick_oe else []
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
