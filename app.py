from __future__ import annotations

import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash

from app.excel_io import generate_excel_with_bld, preview_inquiry_columns
from app.catalog_export import export_products_xlsx
from app.price_import import decode_rows, encode_rows, parse_price_file
from app.material_sheet import create_plan_template, generate_material_sheet_from_materials, material_data_stats
from app.database import (
    bootstrap_materials_from_excel,
    bootstrap_from_excel,
    connect,
    deactivate_material_item,
    deactivate_product,
    delete_alias,
    get_material_item,
    get_product,
    get_user,
    get_user_by_username,
    import_catalog,
    import_materials_from_excel,
    ensure_default_admin,
    list_audit_logs,
    list_log_actors,
    list_aliases,
    list_material_items,
    list_products,
    list_users,
    log_event,
    material_item_stats,
    append_product_code,
    save_user,
    product_stats,
    rows_for_material_sheet,
    rows_for_catalog,
    save_alias,
    upsert_material_item,
    upsert_product,
)
from app.matcher import ProductCatalog, catalog_summary, load_manual_map, normalize_code


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
CATALOG_PATH = DATA_DIR / "catalog.xlsx"
MANUAL_MAP_PATH = DATA_DIR / "manual_map.json"
DB_PATH = DATA_DIR / "products.sqlite3"
MATERIAL_DATA_PATH = DATA_DIR / "stamping_materials.xlsx"
MATERIAL_TEMPLATE_PATH = DATA_DIR / "production_plan_template.xlsx"


app = Flask(__name__)
app.secret_key = "local-product-matcher"


ROLE_LABELS = {
    "admin": "管理员",
    "editor": "编辑员",
    "user": "普通用户",
    "viewer": "只读用户",
}

ROLE_PERMISSIONS = {
    "admin": {
        "manage_users",
        "import_catalog",
        "edit_products",
        "manage_aliases",
        "generate_match",
        "view_logs",
        "generate_material_sheet",
        "manage_materials",
    },
    "editor": {"edit_products", "manage_aliases", "generate_match", "view_logs", "generate_material_sheet"},
    "user": {"generate_match", "generate_material_sheet"},
    "viewer": set(),
}


def actor_name() -> str:
    user = getattr(g, "user", None)
    if not user:
        return ""
    return user["username"]


def can(permission: str) -> bool:
    user = getattr(g, "user", None)
    if not user:
        return False
    return permission in ROLE_PERMISSIONS.get(user["role"], set())


app.jinja_env.globals["can"] = can
app.jinja_env.globals["ROLE_LABELS"] = ROLE_LABELS


def product_image_url(product) -> str:
    explicit = (product["image_path"] if "image_path" in product.keys() else "") or ""
    if explicit:
        if explicit.startswith(("http://", "https://", "/static/")):
            return explicit
        return url_for("static", filename=explicit.lstrip("/"))

    bld_no = product["bld_no"] if "bld_no" in product.keys() else ""
    for suffix in ("jpg", "jpeg", "png", "webp"):
        relative = f"product_images/{bld_no}.{suffix}"
        if (BASE_DIR / "static" / relative).exists():
            return url_for("static", filename=relative)
    return ""


app.jinja_env.globals["product_image_url"] = product_image_url


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)

    return wrapper


def permission_required(permission: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not getattr(g, "user", None):
                return redirect(url_for("login", next=request.path))
            if not can(permission):
                flash("当前账号没有权限执行这个操作。", "error")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@app.before_request
def load_current_user():
    with connect(DB_PATH) as conn:
        ensure_default_admin(conn)
        user_id = session.get("user_id")
        g.user = get_user(conn, int(user_id)) if user_id else None
    if request.endpoint in {"login", "do_login", "static"}:
        return
    if request.endpoint and not g.user:
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
    if g.user and not g.user["active"]:
        session.clear()
        flash("账号已停用。", "error")
        return redirect(url_for("login"))


def _bootstrap_catalog() -> None:
    if CATALOG_PATH.exists():
        return
    candidates = sorted((BASE_DIR / "产品目录").glob("*.xlsx"))
    if candidates:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidates[0], CATALOG_PATH)


def _load_catalog() -> ProductCatalog | None:
    _bootstrap_catalog()
    bootstrap_from_excel(DB_PATH, CATALOG_PATH)
    with connect(DB_PATH) as conn:
        products, aliases = rows_for_catalog(conn)
    if not products:
        return None
    legacy_map = load_manual_map(MANUAL_MAP_PATH)
    aliases.update(legacy_map)
    return ProductCatalog(products, manual_map=aliases)


def _safe_upload_name(filename: str) -> str:
    name = secure_filename(filename)
    suffix = Path(filename).suffix.lower()
    if not name:
        return f"upload-{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}"
    if suffix and not Path(name).suffix:
        return f"{name}{suffix}"
    return name


def _clean_original_filename(filename: str, fallback_suffix: str = "") -> str:
    name = Path(filename or "").name.replace("/", "").replace("\\", "").strip()
    if not name:
        name = f"source{fallback_suffix}"
    if fallback_suffix and not Path(name).suffix:
        name = f"{name}{fallback_suffix}"
    return name


def _result_output_path(original_filename: str, fallback_suffix: str = "") -> Path:
    source_name = _clean_original_filename(original_filename, fallback_suffix=fallback_suffix)
    prefix = f"re{datetime.now().strftime('%y%m%d')}"
    candidate = OUTPUT_DIR / f"{prefix}{source_name}"
    if not candidate.exists():
        return candidate

    stem = Path(source_name).stem
    suffix = Path(source_name).suffix
    counter = 2
    while True:
        numbered = OUTPUT_DIR / f"{prefix}{stem}_{counter}{suffix}"
        if not numbered.exists():
            return numbered
        counter += 1


def _column_display(index: int) -> str:
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


@app.get("/login")
def login():
    return render_template("login.html", next=request.args.get("next", ""))


@app.post("/login")
def do_login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    with connect(DB_PATH) as conn:
        user = get_user_by_username(conn, username)
    if not user or not user["active"] or not check_password_hash(user["password_hash"], password):
        flash("登录名或密码不正确。", "error")
        return redirect(url_for("login"))
    session["user_id"] = user["id"]
    flash("登录成功。", "success")
    return redirect(request.form.get("next") or url_for("index"))


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("已退出登录。", "success")
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    catalog = _load_catalog()
    with connect(DB_PATH) as conn:
        stats = product_stats(conn)
    latest_outputs = sorted(OUTPUT_DIR.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)[:8] if OUTPUT_DIR.exists() else []
    return render_template(
        "index.html",
        catalog_summary=catalog_summary(catalog) if catalog else None,
        product_stats=stats,
        catalog_path=CATALOG_PATH if CATALOG_PATH.exists() else None,
        latest_outputs=latest_outputs,
    )


@app.get("/materials")
@login_required
def materials():
    bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
    query = request.args.get("q", "")
    status = request.args.get("status", "active")
    if status not in {"active", "all", "inactive"}:
        status = "active"
    with connect(DB_PATH) as conn:
        items = list_material_items(
            conn,
            query=query,
            include_inactive=status == "all",
            only_inactive=status == "inactive",
            limit=3000,
        )
        stats = material_item_stats(conn)
    latest_outputs = (
        sorted(OUTPUT_DIR.glob("*料单*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)[:8]
        if OUTPUT_DIR.exists()
        else []
    )
    return render_template(
        "materials.html",
        material_file_stats=material_data_stats(MATERIAL_DATA_PATH),
        material_stats=stats,
        material_items=items,
        material_path=MATERIAL_DATA_PATH if MATERIAL_DATA_PATH.exists() else None,
        query=query,
        status=status,
        latest_outputs=latest_outputs,
    )


@app.get("/materials/template")
@login_required
def download_material_template():
    create_plan_template(MATERIAL_TEMPLATE_PATH)
    return send_file(MATERIAL_TEMPLATE_PATH, as_attachment=True, download_name="生产计划模板.xlsx")


@app.post("/materials/generate")
@permission_required("generate_material_sheet")
def generate_materials():
    file = request.files.get("plan")
    if not file or not file.filename:
        flash("请选择生产计划 Excel 文件。", "error")
        return redirect(url_for("materials"))
    if Path(file.filename).suffix.lower() != ".xlsx":
        flash("生产计划请使用 .xlsx 文件。", "error")
        return redirect(url_for("materials"))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = UPLOAD_DIR / f"material-plan-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_safe_upload_name(file.filename)}"
    file.save(upload_path)

    try:
        bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
        with connect(DB_PATH) as conn:
            material_rows = rows_for_material_sheet(conn)
        if not material_rows:
            raise ValueError("还没有可用的材料明细，请先上传或新增材料数据。")
        output_path, summary = generate_material_sheet_from_materials(material_rows, upload_path, OUTPUT_DIR)
        with connect(DB_PATH) as conn:
            missing_text = f"，未匹配 {len(summary['missing'])} 个型号" if summary["missing"] else ""
            log_event(
                conn,
                "生成生产料单",
                "material_sheet",
                output_path.name,
                f"生产计划 {summary['plan_count']} 行，料单明细 {summary['detail_count']} 行，规格 {summary['spec_count']} 个{missing_text}",
                actor=actor_name(),
            )
            conn.commit()
    except Exception as exc:
        flash(f"生成失败：{exc}", "error")
        return redirect(url_for("materials"))

    return send_file(output_path, as_attachment=True)


@app.post("/materials/data")
@permission_required("manage_materials")
def upload_material_data():
    file = request.files.get("material_data")
    if not file or not file.filename:
        flash("请选择材料数据 Excel 文件。", "error")
        return redirect(url_for("materials"))
    if Path(file.filename).suffix.lower() != ".xlsx":
        flash("材料数据请使用 .xlsx 文件。", "error")
        return redirect(url_for("materials"))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = UPLOAD_DIR / f"material-data-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_safe_upload_name(file.filename)}"
    file.save(upload_path)
    stats = material_data_stats(upload_path)
    if stats.get("invalid"):
        flash(f"材料数据读取失败：{stats.get('error') or '文件里必须包含“材料数据”工作表。'}", "error")
        return redirect(url_for("materials"))

    if MATERIAL_DATA_PATH.exists():
        backup = DATA_DIR / f"stamping_materials-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
        shutil.copy2(MATERIAL_DATA_PATH, backup)
    shutil.copy2(upload_path, MATERIAL_DATA_PATH)
    try:
        with connect(DB_PATH) as conn:
            imported = import_materials_from_excel(conn, upload_path, replace=True, actor=actor_name())
            log_event(
                conn,
                "更新材料数据文件",
                "material_data",
                _clean_original_filename(file.filename, fallback_suffix=".xlsx"),
                f"型号 {stats['model_count']} 个，明细 {stats['detail_count']} 行；导入数据库 {imported} 行",
                actor=actor_name(),
            )
            conn.commit()
    except Exception as exc:
        flash(f"材料数据导入失败：{exc}", "error")
        return redirect(url_for("materials"))
    flash("材料数据已更新并导入数据库。", "success")
    return redirect(url_for("materials"))


@app.get("/materials/items/new")
@permission_required("manage_materials")
def new_material_item():
    return render_template("material_item_form.html", item=None)


@app.get("/materials/items/<int:item_id>/edit")
@permission_required("manage_materials")
def edit_material_item(item_id: int):
    bootstrap_materials_from_excel(DB_PATH, MATERIAL_DATA_PATH)
    with connect(DB_PATH) as conn:
        item = get_material_item(conn, item_id)
    if not item:
        flash("材料明细不存在。", "error")
        return redirect(url_for("materials"))
    return render_template("material_item_form.html", item=item)


@app.post("/materials/items/save")
@permission_required("manage_materials")
def save_material_item():
    data = {
        "id": request.form.get("id", ""),
        "model": request.form.get("model", ""),
        "code": request.form.get("code", ""),
        "category": request.form.get("category", ""),
        "car": request.form.get("car", ""),
        "part": request.form.get("part", ""),
        "spec_text": request.form.get("spec_text", ""),
        "pieces": request.form.get("pieces", ""),
        "thickness": request.form.get("thickness", ""),
        "width": request.form.get("width", ""),
        "length": request.form.get("length", ""),
        "active": request.form.get("active", "0"),
    }
    try:
        with connect(DB_PATH) as conn:
            upsert_material_item(conn, data, actor=actor_name())
    except Exception as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("materials"))
    flash("材料明细已保存。", "success")
    return redirect(url_for("materials", q=data["model"]))


@app.post("/materials/items/<int:item_id>/deactivate")
@permission_required("manage_materials")
def stop_material_item(item_id: int):
    with connect(DB_PATH) as conn:
        deactivate_material_item(conn, item_id, actor=actor_name())
    flash("材料明细已停用。", "success")
    return redirect(url_for("materials"))


@app.get("/users")
@permission_required("manage_users")
def users():
    with connect(DB_PATH) as conn:
        rows = list_users(conn)
    return render_template("users.html", users=rows, editing=None)


@app.get("/users/<int:user_id>/edit")
@permission_required("manage_users")
def edit_user(user_id: int):
    with connect(DB_PATH) as conn:
        rows = list_users(conn)
        editing = get_user(conn, user_id)
    if not editing:
        flash("账号不存在。", "error")
        return redirect(url_for("users"))
    return render_template("users.html", users=rows, editing=editing)


@app.post("/users/save")
@permission_required("manage_users")
def save_user_route():
    data = {
        "id": request.form.get("id", ""),
        "username": request.form.get("username", ""),
        "display_name": request.form.get("display_name", ""),
        "role": request.form.get("role", "viewer"),
        "active": request.form.get("active", "0"),
        "password": request.form.get("password", ""),
    }
    try:
        with connect(DB_PATH) as conn:
            save_user(conn, data, actor=actor_name())
    except Exception as exc:
        flash(f"账号保存失败：{exc}", "error")
        return redirect(url_for("users"))
    flash("账号已保存。", "success")
    return redirect(url_for("users"))


@app.post("/catalog")
@permission_required("import_catalog")
def upload_catalog():
    redirect_target = url_for("products") if request.form.get("next") == "products" else url_for("index")
    file = request.files.get("catalog")
    if not file or not file.filename:
        flash("请选择产品目录 Excel 文件。", "error")
        return redirect(redirect_target)
    if not file.filename.lower().endswith(".xlsx"):
        flash("产品目录请使用 .xlsx 文件。", "error")
        return redirect(redirect_target)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backup = DATA_DIR / f"catalog-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
    if CATALOG_PATH.exists():
        shutil.copy2(CATALOG_PATH, backup)
    file.save(CATALOG_PATH)
    try:
        with connect(DB_PATH) as conn:
            import_catalog(conn, CATALOG_PATH, replace=False, actor=actor_name())
    except Exception as exc:
        if backup.exists():
            shutil.copy2(backup, CATALOG_PATH)
        flash(f"目录读取失败，已恢复旧目录：{exc}", "error")
        return redirect(redirect_target)

    flash("产品目录已导入。已有 BLD NO. 会更新，新增 BLD NO. 会加入产品库。", "success")
    return redirect(redirect_target)


@app.post("/match")
@permission_required("generate_match")
def match_inquiry():
    catalog = _load_catalog()
    if not catalog:
        flash("请先上传产品目录。", "error")
        return redirect(url_for("index"))

    file = request.files.get("inquiry")
    if not file or not file.filename:
        flash("请选择客户询价文件。", "error")
        return redirect(url_for("index"))
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        flash("客户源文件支持 .xls 和 .xlsx，并会保持格式新增列。", "error")
        return redirect(url_for("index"))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_upload_name(file.filename)
    upload_path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_name}"
    file.save(upload_path)

    output_path = _result_output_path(file.filename, fallback_suffix=suffix)
    output_name = output_path.name
    try:
        summary = generate_excel_with_bld(upload_path, output_path, catalog)
        with connect(DB_PATH) as conn:
            log_event(
                conn,
                "生成匹配结果",
                "inquiry",
                _clean_original_filename(file.filename, fallback_suffix=suffix),
                f"共 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行",
                actor=actor_name(),
            )
            conn.commit()
    except Exception as exc:
        if "询价表没有找到可识别表头" in str(exc):
            preview = preview_inquiry_columns(upload_path)
            return render_template(
                "select_match_column.html",
                upload_path=upload_path,
                original_filename=_clean_original_filename(file.filename, fallback_suffix=suffix),
                output_name=output_name,
                preview=preview,
            )
        flash(f"生成失败：{exc}", "error")
        return redirect(url_for("index"))

    return render_template("result.html", summary=summary, output_path=output_path)


@app.post("/match/column")
@permission_required("generate_match")
def match_inquiry_with_column():
    catalog = _load_catalog()
    if not catalog:
        flash("请先上传产品目录。", "error")
        return redirect(url_for("index"))

    upload_path = Path(request.form.get("upload_path", "")).resolve()
    if UPLOAD_DIR.resolve() not in upload_path.parents or not upload_path.exists():
        flash("询价源文件不存在，请重新上传。", "error")
        return redirect(url_for("index"))

    try:
        match_column = int(request.form.get("match_column", "0"))
    except ValueError:
        flash("请选择有效的匹配列。", "error")
        return redirect(url_for("index"))

    original_filename = request.form.get("original_filename") or upload_path.name
    output_name = request.form.get("output_name")
    output_path = OUTPUT_DIR / Path(output_name).name if output_name else _result_output_path(original_filename, fallback_suffix=upload_path.suffix)
    try:
        summary = generate_excel_with_bld(upload_path, output_path, catalog, match_column=match_column)
        with connect(DB_PATH) as conn:
            log_event(
                conn,
                "生成匹配结果",
                "inquiry",
                original_filename,
                f"手动选择 {_column_display(match_column)} 列；共 {summary['total']} 行，命中 {summary['matched']} 行，未找到 {summary['unmatched']} 行",
                actor=actor_name(),
            )
            conn.commit()
    except Exception as exc:
        flash(f"生成失败：{exc}", "error")
        return redirect(url_for("index"))

    return render_template("result.html", summary=summary, output_path=output_path)


@app.post("/manual-map")
@permission_required("manage_aliases")
def add_manual_map():
    source_code = request.form.get("source_code", "")
    bld_no = request.form.get("bld_no", "")
    if not normalize_code(source_code) or not normalize_code(bld_no):
        flash("请输入客户号码和 BLD NO.。", "error")
        return redirect(url_for("index"))

    with connect(DB_PATH) as conn:
        save_alias(conn, source_code, bld_no, request.form.get("note", ""), actor=actor_name())
        appended = False
        sync_target = request.form.get("sync_target", "oe")
        if sync_target in {"oe", "brand_code"}:
            appended = append_product_code(conn, bld_no, source_code, target=sync_target, actor=actor_name())
    target_label = "OE 号" if request.form.get("sync_target", "oe") == "oe" else "品牌号码"
    flash("人工映射已保存。" + (f" 已同步加入产品目录{target_label}。" if appended else ""), "success")
    return redirect(url_for("index"))


@app.post("/manual-map/delete")
@permission_required("manage_aliases")
def delete_manual_map():
    source_code = request.form.get("source_code", "")
    with connect(DB_PATH) as conn:
        delete_alias(conn, source_code, actor=actor_name())
    flash("人工映射已删除。", "success")
    return redirect(url_for("index"))


@app.get("/products")
@login_required
def products():
    query = request.args.get("q", "")
    bld_query = request.args.get("bld", "")
    oe_query = request.args.get("oe", "")
    if oe_query.strip():
        bld_query = ""
    status = request.args.get("status", "active")
    if status not in {"active", "all", "inactive"}:
        status = "active"
    with connect(DB_PATH) as conn:
        bootstrap_from_excel(DB_PATH, CATALOG_PATH)
        rows = list_products(
            conn,
            query=query,
            bld_query=bld_query,
            oe_query=oe_query,
            include_inactive=status == "all",
            only_inactive=status == "inactive",
            limit=3000,
        )
        stats = product_stats(conn)
    return render_template(
        "products.html",
        products=rows,
        query=query,
        bld_query=bld_query or query,
        oe_query=oe_query,
        status=status,
        stats=stats,
    )


@app.get("/products/export")
@login_required
def export_products_options():
    status = request.args.get("status", "all")
    return render_template("export_catalog.html", status=status)


@app.post("/products/export")
@login_required
def export_products():
    status = request.form.get("status", "all")
    include_inactive = status != "active"
    export_format = request.form.get("export_format", "bld")
    if export_format not in {"bld", "brand"}:
        export_format = "bld"
    format_label = "brand" if export_format == "brand" else "bld"
    output_path = OUTPUT_DIR / f"catalog-export-{format_label}-{datetime.now().strftime('%y%m%d')}.xlsx"
    counter = 2
    while output_path.exists():
        output_path = OUTPUT_DIR / f"catalog-export-{format_label}-{datetime.now().strftime('%y%m%d')}_{counter}.xlsx"
        counter += 1
    with connect(DB_PATH) as conn:
        export_products_xlsx(conn, output_path, include_inactive=include_inactive, export_format=export_format)
        log_event(
            conn,
            "导出目录",
            "catalog",
            output_path.name,
            ("按汽车品牌格式；" if export_format == "brand" else "按 BLD 号格式；")
            + ("包含停用产品" if include_inactive else "仅启用产品"),
            actor=actor_name(),
        )
        conn.commit()
    return send_file(output_path, as_attachment=True)


@app.get("/prices/import")
@permission_required("edit_products")
def price_import():
    return render_template("price_import.html", preview=None)


@app.post("/prices/import/preview")
@permission_required("edit_products")
def price_import_preview():
    file = request.files.get("price_file")
    if not file or not file.filename:
        flash("请选择单价 Excel 文件。", "error")
        return redirect(url_for("price_import"))
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        flash("单价导入文件支持 .xls 和 .xlsx。", "error")
        return redirect(url_for("price_import"))

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = UPLOAD_DIR / f"price-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_safe_upload_name(file.filename)}"
    file.save(upload_path)
    try:
        with connect(DB_PATH) as conn:
            preview = parse_price_file(upload_path, conn)
    except Exception as exc:
        flash(f"解析失败：{exc}", "error")
        return redirect(url_for("price_import"))
    return render_template("price_import.html", preview=preview, payload=encode_rows(preview["rows"]))


@app.post("/prices/import/apply")
@permission_required("edit_products")
def price_import_apply():
    try:
        rows = decode_rows(request.form.get("payload", "[]"))
    except Exception as exc:
        flash(f"导入数据无效：{exc}", "error")
        return redirect(url_for("price_import"))

    updated = 0
    skipped = 0
    with connect(DB_PATH) as conn:
        for row in rows:
            if row.get("status") != "matched":
                skipped += 1
                continue
            conn.execute(
                "UPDATE products SET price_cny = ?, updated_at = ? WHERE bld_no = ?",
                (row["price"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row["bld_no"]),
            )
            updated += 1
        log_event(conn, "批量维护单价", "product", "Unit Price", f"更新 {updated} 条，跳过 {skipped} 条", actor=actor_name())
        conn.commit()
    flash(f"单价导入完成：更新 {updated} 条，跳过 {skipped} 条。", "success")
    return redirect(url_for("products"))


@app.get("/logs")
@permission_required("view_logs")
def logs():
    query = request.args.get("q", "")
    actor = request.args.get("actor", "")
    with connect(DB_PATH) as conn:
        rows = list_audit_logs(conn, query=query, actor=actor)
        actors = list_log_actors(conn)
    return render_template("logs.html", logs=rows, query=query, actor=actor, actors=actors)


@app.get("/products/new")
@permission_required("edit_products")
def new_product():
    return render_template("product_form.html", product=None)


@app.get("/products/<int:product_id>/edit")
@permission_required("edit_products")
def edit_product(product_id: int):
    with connect(DB_PATH) as conn:
        product = get_product(conn, product_id)
    if not product:
        flash("产品不存在。", "error")
        return redirect(url_for("products"))
    return render_template("product_form.html", product=product)


@app.post("/products/save")
@permission_required("edit_products")
def save_product():
    data = {
        "bld_no": request.form.get("bld_no", ""),
        "series": request.form.get("series", ""),
        "item": request.form.get("item", ""),
        "oe_no_1": request.form.get("oe_no_1", ""),
        "oe_no_2": request.form.get("oe_no_2", ""),
        "models": request.form.get("models", ""),
        "price_cny": request.form.get("price_cny", ""),
        "image_path": request.form.get("image_path", ""),
        "active": request.form.get("active", "0"),
    }
    try:
        with connect(DB_PATH) as conn:
            upsert_product(conn, data, source="web", actor=actor_name())
    except Exception as exc:
        flash(f"保存失败：{exc}", "error")
        return redirect(url_for("products"))
    flash("产品已保存。", "success")
    return redirect(url_for("products", q=data["bld_no"]))


@app.post("/products/<int:product_id>/deactivate")
@permission_required("edit_products")
def stop_product(product_id: int):
    with connect(DB_PATH) as conn:
        deactivate_product(conn, product_id, actor=actor_name())
    flash("产品已停用，历史资料仍保留。", "success")
    return redirect(url_for("products"))


@app.get("/download/<path:name>")
@login_required
def download(name: str):
    path = (OUTPUT_DIR / name).resolve()
    if OUTPUT_DIR.resolve() not in path.parents or not path.exists():
        flash("文件不存在。", "error")
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
