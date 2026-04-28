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

CATALOG_PATH = DATA_DIR / "catalog.xlsx"
MANUAL_MAP_PATH = DATA_DIR / "manual_map.json"
DB_PATH = DATA_DIR / "products.sqlite3"
MATERIAL_DATA_PATH = DATA_DIR / "stamping_materials.xlsx"
MATERIAL_TEMPLATE_PATH = DATA_DIR / "production_plan_template.xlsx"

SECRET_KEY = os.environ.get("SECRET_KEY", "local-product-matcher")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "5055"))
APP_DEBUG = os.environ.get("APP_DEBUG", "").lower() in {"1", "true", "yes", "on"}
