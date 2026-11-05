"""Container-only release switch and Gunicorn worker entry point (loaded from the image)."""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

IMAGE = Path("/app")
STORE = Path("/srv/bld-releases")
CURRENT = STORE / "current"
PENDING = STORE / "previous-pending"
REVISION = re.compile(r"[a-f0-9]{40}(?:-[a-f0-9]{12})?")
HEALTH = "http://127.0.0.1:8000"
WORKERS = 2


def selected() -> str:
    return CURRENT.read_text().strip() if CURRENT.exists() else "image"


def root_for(version: str) -> Path:
    if version == "image":
        return IMAGE
    if not REVISION.fullmatch(version):
        raise ValueError("发布版本格式无效")
    return STORE / version


def record(path: Path, version: str) -> None:
    root_for(version)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(version + "\n")
    tmp.replace(path)


def load_app():
    """Gunicorn forks before calling this factory, so old workers keep their own files."""
    version = selected()
    root = root_for(version)
    os.chdir(root)
    worker_marker = STORE / f"worker-{os.getpid()}"
    worker_marker.unlink(missing_ok=True)
    sys.path.insert(0, str(root))
    os.environ["BLD_INQUIRY_BASE_DIR"] = str(IMAGE)
    try:
        from wsgi import app
        worker_marker.touch()
    except Exception:
        if version == "image":
            raise
        # Keep the master alive so the deployer can switch back after a bad import.
        logging.exception("Cannot load candidate %s", version)

        def app(environ, start_response):
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"Release failed to load"]

    def serve(environ, start_response):
        if environ.get("PATH_INFO") != "/health/live":
            return app(environ, start_response)

        def mark(status, headers, exc_info=None):
            return start_response(status, [*headers, ("X-BLD-Release", version)], exc_info)

        return app(environ, mark)

    return serve


def unpack(stream, dest: Path) -> None:
    # Only ordinary files and directories in a fresh directory. Never follow links
    # or copy ownership, modes, or special files from an archive into the container.
    size = count = 0
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in entry.name
                    or path.parts[0] in {".git", "data", "uploads", "outputs", "logs", ".env"}
                    or any(p.startswith(".env.") and p != ".env.example" for p in path.parts)):
                raise ValueError(f"禁止的发布路径 {entry.name!r}")
            target = dest / path
            size += entry.size
            count += 1
            if size > 256 * 1024**2 or count > 24000 or entry.size > 64 * 1024**2:
                raise ValueError("代码包超过快速更新大小限制")
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not entry.isfile():
                raise ValueError(f"发布包只允许普通文件 {entry.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                source = archive.extractfile(entry)
                assert source is not None
                shutil.copyfileobj(source, output)


def check_workers(version: str) -> None:
    target = root_for(version)
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        try:
            children = Path("/proc/1/task/1/children").read_text().split()
            if len(children) == WORKERS and all(Path(f"/proc/{pid}/cwd").resolve(strict=True) == target
                                                for pid in children):
                with urllib.request.urlopen(HEALTH + "/health/live", timeout=3) as response:
                    if response.headers.get("X-BLD-Release") == version and all(
                        (STORE / f"worker-{pid}").exists() for pid in children
                    ):
                        break
        except urllib.error.HTTPError as exc:
            if exc.headers.get("X-BLD-Release") == version:
                raise RuntimeError(f"新版 worker 返回 HTTP {exc.code}") from exc
        except (OSError, TimeoutError):
            pass
        time.sleep(0.25)
    else:
        raise RuntimeError(f"等待两个 worker 加载 {version} 超时")
    subprocess.run([sys.executable, "-m", "scripts.runtime_probe", "--base-url", HEALTH],
                   cwd=IMAGE, timeout=25, check=True)


def switch(version: str) -> None:
    record(CURRENT, version)
    os.kill(1, signal.SIGHUP)
    check_workers(version)


def apply(revision: str) -> None:
    if not REVISION.fullmatch(revision):
        raise ValueError("需要完整 Git SHA 或带内容哈希的工作树版本")
    STORE.mkdir(parents=True, exist_ok=True)
    with (STORE / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if PENDING.exists():
            switch(PENDING.read_text().strip())
            PENDING.unlink()
        old = selected()
        candidate = root_for(revision)
        with tempfile.TemporaryDirectory(dir=STORE) as stage:
            source = Path(stage) / "source"
            source.mkdir()
            unpack(sys.stdin.buffer, source)
            staged = Path(stage) / "release"
            previous = ["--previous", str(root_for(old) / ".quick-baseline.json")] if old != "image" else []
            subprocess.run([sys.executable, str(IMAGE / "tools/quick_release.py"), "prepare",
                            "--source", str(source), "--baseline", str(IMAGE / ".quick-baseline.json"),
                            "--output", str(staged), "--revision", revision, *previous], check=True)
            if candidate.exists():
                if (candidate / ".quick-baseline.json").read_bytes() != (staged / ".quick-baseline.json").read_bytes():
                    raise RuntimeError("相同发布版本对应不同代码，拒绝切换")
            else:
                staged.rename(candidate)
        if old == revision:
            check_workers(revision)
            print(f"已经运行 {revision}")
            return
        record(PENDING, old)
        try:
            switch(revision)
        except BaseException:
            print(f"新版未通过，正在恢复 {old}", file=sys.stderr, flush=True)
            switch(old)
            PENDING.unlink()
            shutil.rmtree(candidate)
            raise
        PENDING.unlink()
        for path in STORE.iterdir():
            if path.name.startswith("worker-") and not Path("/proc", path.name[7:]).exists():
                path.unlink()
            elif REVISION.fullmatch(path.name) and path.name not in (revision, old):
                shutil.rmtree(path)
        print(f"快速更新完成 {old} -> {revision}", flush=True)


def start() -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    for marker in STORE.glob("worker-*"):
        marker.unlink()
    if PENDING.exists():
        record(CURRENT, PENDING.read_text().strip())
        PENDING.unlink()
    subprocess.run([sys.executable, "-m", "scripts.init_database"], cwd=IMAGE, check=True)
    os.chdir(IMAGE)
    os.execlp("gunicorn", "gunicorn", "--bind", "0.0.0.0:8000", "--workers", str(WORKERS),
              "--timeout", "120", "tools.quick_runtime:load_app()")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("start")
    actions.add_parser("apply").add_argument("revision")
    options = parser.parse_args()
    if options.action == "start":
        start()
    else:
        try:
            apply(options.revision)
        except BlockingIOError:
            sys.exit("已有快速更新正在执行，请等该次发布返回结果。")
        except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.SubprocessError) as exc:
            sys.exit(f"快速更新未完成：{exc}")
