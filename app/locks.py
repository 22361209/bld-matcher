from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR


class ImportLockError(RuntimeError):
    pass


@contextmanager
def import_lock(actor: str, label: str = "全局导入"):
    lock_dir = DATA_DIR / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "import.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            current = handle.read().strip()
            detail = f"：{current}" if current else ""
            raise ImportLockError(f"当前已有用户正在执行导入操作，请稍后再试{detail}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{label} / {actor or 'unknown'} / {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
