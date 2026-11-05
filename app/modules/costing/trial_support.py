"""File-backed settings for the isolated, local cost acceptance workspace."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from pathlib import Path

from flask import current_app

from .service import CostingService


ROOT = Path(__file__).resolve().parents[3]
CONFIG_NAME = "access.json"
DATABASE_NAME = "costing.sqlite3"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".costing-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_settings(directory: Path) -> dict:
    path = Path(directory).resolve() / CONFIG_NAME
    if not path.is_file():
        raise ValueError("成本试用环境尚未初始化，请先运行 init。")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format_version") != 1:
        raise ValueError("成本试用配置版本不受支持。")
    for key in ("password_hash", "secret_key", "credential_id"):
        if not isinstance(value.get(key), str) or len(value[key]) < 16:
            raise ValueError("成本试用访问配置不完整。")
    return value


def service() -> CostingService:
    return current_app.extensions["costing_service"]


def imports():
    return current_app.extensions["costing_imports"]


def new_owner_id() -> str:
    return secrets.token_urlsafe(24)
