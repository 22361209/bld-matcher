from __future__ import annotations

from flask import flash, redirect, render_template, send_file, url_for

from app.config import CATALOG_PATH, DB_PATH, OUTPUT_DIR
from app.database import connect, product_stats
from app.helpers import load_catalog
from app.matcher import catalog_summary
from app.security import login_required


def register(app) -> None:
    @app.get("/")
    @login_required
    def index():
        catalog = load_catalog()
        with connect(DB_PATH) as conn:
            stats = product_stats(conn)
        latest_outputs = (
            sorted(OUTPUT_DIR.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)[:8]
            if OUTPUT_DIR.exists()
            else []
        )
        return render_template(
            "index.html",
            catalog_summary=catalog_summary(catalog) if catalog else None,
            product_stats=stats,
            catalog_path=CATALOG_PATH if CATALOG_PATH.exists() else None,
            latest_outputs=latest_outputs,
        )

    @app.get("/download/<path:name>")
    @login_required
    def download(name: str):
        path = (OUTPUT_DIR / name).resolve()
        if OUTPUT_DIR.resolve() not in path.parents or not path.exists():
            flash("文件不存在。", "error")
            return redirect(url_for("index"))
        return send_file(path, as_attachment=True)
