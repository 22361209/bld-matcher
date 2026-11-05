#!/usr/bin/env python3
"""Manage the isolated, loopback-only costing trial without touching port 5055."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
DEFAULT_DATA_DIR = ROOT / "data" / "costing-trial"
DEFAULT_PORT = 5056
HOST = "127.0.0.1"
STATE_FILE = "launcher.json"
START_TIMEOUT = 10.0
STOP_TIMEOUT = 5.0

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class LauncherError(RuntimeError):
    pass


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须是整数。") from exc
    if port == 5055 or not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("试用端口必须在 1024–65535 之间，不能使用正式服务的 5055。")
    return port


def _url(port: int) -> str:
    return f"http://{HOST}:{port}"


@contextmanager
def _lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (data_dir / ".launcher.lock").open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LauncherError("另一个启动或停止操作尚未完成，请稍后重试。") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_state(data_dir: Path) -> dict | None:
    path = data_dir / STATE_FILE
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or type(value.get("pid")) is not int
            or value["pid"] <= 1
            or type(value.get("port")) is not int
            or value["port"] == 5055
            or not 1024 <= value["port"] <= 65535
            or not isinstance(value.get("instance_id"), str)
            or len(value["instance_id"]) != 32
        ):
            raise ValueError("invalid launcher state")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise LauncherError("试用进程记录无效，已停止操作以免影响其他服务。") from exc


def _write_state(data_dir: Path, state: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".launcher-", dir=data_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, data_dir / STATE_FILE)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_matches(state: dict) -> bool:
    """A stale/reused PID must never authorize terminating a different process."""
    result = subprocess.run(
        ["ps", "-p", str(state["pid"]), "-o", "args="],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    command = result.stdout
    return bool(
        result.returncode == 0
        and str(SCRIPT) in command
        and "serve" in command
        and f"--instance-id {state['instance_id']}" in command
    )


def _health(state: dict) -> bool:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"{_url(state['port'])}/health/ready", timeout=0.5) as response:
            payload = json.loads(response.read(65_536))
        return payload.get("ok") is True and payload.get("instance_id") == state["instance_id"]
    except (OSError, URLError, ValueError, AttributeError):
        return False


def _check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            # Match the server's restart behavior for recently closed connections.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((HOST, port))
            probe.listen(1)
        except OSError as exc:
            raise LauncherError(f"{port} 端口已被占用；请选择其他试用端口。") from exc


def initialize(data_dir: Path) -> dict:
    from app.modules.costing.trial_app import initialize_trial

    result = initialize_trial(data_dir, password=os.environ.get("BLD_COSTING_TRIAL_PASSWORD") or None)
    if result.get("created"):
        print("独立成本试用数据已初始化。", flush=True)
        if result.get("password"):
            print(f"首次登录口令（仅显示一次）：{result['password']}", flush=True)
    else:
        print("独立成本试用数据已存在。", flush=True)
    return result


def start(data_dir: Path, port: int, *, read_only: bool = False) -> dict:
    with _lock(data_dir):
        old = _read_state(data_dir)
        if old and _pid_exists(old["pid"]):
            if not _process_matches(old):
                raise LauncherError("记录的 PID 不属于本试用服务，已拒绝操作其他进程。")
            if not _health(old):
                raise LauncherError("试用进程仍在运行但尚未就绪；请检查日志，或先使用 stop 正常停止。")
            if old["port"] != port or bool(old.get("read_only")) != read_only:
                raise LauncherError("现有试用服务的端口或只读设置不同，请先 stop 后再 start。")
            print(f"成本试用已运行：{_url(old['port'])}/", flush=True)
            return old
        _check_port(port)
        initialize(data_dir)
        state = {
            "instance_id": secrets.token_hex(16),
            "port": port,
            "read_only": read_only,
            "started_at": time.time(),
        }
        command = [
            sys.executable, str(SCRIPT), "serve", "--data-dir", str(data_dir),
            "--port", str(port), "--instance-id", state["instance_id"],
        ]
        if read_only:
            command.append("--read-only")
        environment = os.environ.copy()
        environment.pop("BLD_COSTING_TRIAL_PASSWORD", None)
        environment["PYTHONUNBUFFERED"] = "1"
        descriptor = os.open(data_dir / "server.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as log:
            child = subprocess.Popen(
                command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        state["pid"] = child.pid
        _write_state(data_dir, state)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if child.poll() is not None:
                (data_dir / STATE_FILE).unlink(missing_ok=True)
                raise LauncherError("试用服务启动失败，请检查试用目录内的 server.log。")
            if _health(state):
                print(f"成本试用已就绪：{_url(port)}/", flush=True)
                return state
            time.sleep(0.15)
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            raise LauncherError("启动检查超时，试用进程尚未退出；请使用 stop 再次停止并检查日志。") from None
        (data_dir / STATE_FILE).unlink(missing_ok=True)
        raise LauncherError("启动检查超时，已停止本次试用进程。请检查 server.log。")


def stop(data_dir: Path) -> None:
    if not data_dir.exists():
        print("成本试用未运行。")
        return
    with _lock(data_dir):
        state = _read_state(data_dir)
        if not state or not _pid_exists(state["pid"]):
            (data_dir / STATE_FILE).unlink(missing_ok=True)
            print("成本试用未运行。")
            return
        if not _process_matches(state):
            raise LauncherError("记录的 PID 不属于本试用服务，已拒绝停止其他进程。")
        os.kill(state["pid"], signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline:
            if not _pid_exists(state["pid"]) or not _process_matches(state):
                (data_dir / STATE_FILE).unlink(missing_ok=True)
                print("成本试用已停止，成本资料已保留。")
                return
            time.sleep(0.1)
        raise LauncherError("试用进程尚未退出，未强制终止；请稍后检查 status。")


def status(data_dir: Path, *, as_json: bool = False) -> dict:
    state = _read_state(data_dir)
    running = bool(state and _pid_exists(state["pid"]) and _process_matches(state))
    result = {
        "running": running,
        "ready": bool(state is not None and running and _health(state)),
        "pid": state["pid"] if state is not None and running else None,
        "url": f"{_url(state['port'])}/" if state else None,
        "read_only": bool(state and state.get("read_only")),
    }
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["ready"]:
        print(f"成本试用已就绪：{result['url']}（PID {result['pid']}）")
    elif running:
        print(f"成本试用正在运行但未就绪（PID {result['pid']}），请检查 server.log。")
    else:
        print("成本试用未运行。")
    return result


def serve(data_dir: Path, port: int, *, read_only: bool, instance_id: str | None) -> None:
    from app.modules.costing.trial_app import create_trial_app

    application = create_trial_app(data_dir, read_only=read_only)
    application.config["COSTING_TRIAL_INSTANCE_ID"] = instance_id or secrets.token_hex(16)
    # Browser preconnections can stay idle; they must not block other requests.
    application.run(host=HOST, port=port, debug=False, use_reloader=False, threaded=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="管理独立成本核算试用，不操作正式 5055 服务。")
    parser.add_argument("command", choices=("init", "start", "serve", "status", "stop"))
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT)
    parser.add_argument("--read-only", action="store_true", help="以只读方式试用。")
    parser.add_argument("--json", action="store_true", help="status 输出 JSON。")
    parser.add_argument("--instance-id", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    try:
        if args.command == "init":
            with _lock(data_dir):
                initialize(data_dir)
        elif args.command == "start":
            start(data_dir, args.port, read_only=args.read_only)
        elif args.command == "stop":
            stop(data_dir)
        elif args.command == "status":
            status(data_dir, as_json=args.json)
        else:
            serve(data_dir, args.port, read_only=args.read_only, instance_id=args.instance_id)
    except (LauncherError, OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
