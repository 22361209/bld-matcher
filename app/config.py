from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    return Path(value).expanduser().resolve()


_load_env_file(BASE_DIR / ".env")

DATA_DIR = _path_from_env("BLD_DATA_DIR", BASE_DIR / "data")
UPLOAD_DIR = _path_from_env("BLD_UPLOAD_DIR", BASE_DIR / "uploads")
OUTPUT_DIR = _path_from_env("BLD_OUTPUT_DIR", BASE_DIR / "outputs")
DRAWING_DIR = DATA_DIR / "drawings"
DRAWING_PDF_DIR = DRAWING_DIR / "pdf"
DRAWING_ARCHIVE_DIR = DRAWING_DIR / "archive"
PRODUCT_IMAGE_DIR = DATA_DIR / "product_images"
PRODUCT_IMAGE_THUMB_DIR = PRODUCT_IMAGE_DIR / "thumbs"
PRODUCT_IMAGE_ARCHIVE_DIR = PRODUCT_IMAGE_DIR / "archive"
PRODUCT_IMAGE_DATA_PREFIX = "data_product_images/"

CATALOG_PATH = DATA_DIR / "catalog.xlsx"
MANUAL_MAP_PATH = DATA_DIR / "manual_map.json"
DB_PATH = DATA_DIR / "products.sqlite3"
MATERIAL_DATA_PATH = DATA_DIR / "stamping_materials.xlsx"
MATERIAL_TEMPLATE_PATH = DATA_DIR / "production_plan_template.xlsx"

DEFAULT_SECRET_KEY = "local-product-matcher"
SECRET_KEY = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "5055"))
APP_DEBUG = os.environ.get("APP_DEBUG", "").lower() in {"1", "true", "yes", "on"}

# 首启时创建的默认管理员账号。生产部署应通过环境变量覆盖密码,
# 并在登录后立即从后台修改。
DEFAULT_ADMIN_USERNAME = os.environ.get("DEFAULT_ADMIN_USERNAME", "007").strip() or "007"
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "change-me-on-first-login")


def assert_production_secrets() -> None:
    """生产模式(非 DEBUG)下拒绝使用默认 SECRET_KEY 启动。

    DEBUG 模式只警告,不阻断本机开发。
    """
    if SECRET_KEY == DEFAULT_SECRET_KEY:
        message = (
            "SECRET_KEY 仍为默认值,请在 .env 或环境变量中设置一个随机长字符串。"
            "可用 `python -c \"import secrets; print(secrets.token_urlsafe(48))\"` 生成。"
        )
        if not APP_DEBUG:
            raise RuntimeError(message)
        import warnings

        warnings.warn(message, RuntimeWarning, stacklevel=2)
