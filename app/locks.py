from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

try:
    import fcntl
except ModuleNotFoundError:
    fcntl = None
    import msvcrt


class ImportLockError(RuntimeError):
    pass


@contextmanager
def import_lock(actor: str, label: str = "全局导入"):
    lock_dir = DATA_DIR / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "import.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError) as exc:
            current = ""
            if fcntl is not None:
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
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
