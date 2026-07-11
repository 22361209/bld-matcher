from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TYPECHECK_PATHS = (
    "app/platform",
    "app/modules/admin",
    "app/modules/inquiry/alias_web.py",
    "app/modules/inquiry/download_web.py",
    "app/modules/inquiry/excel",
    "app/modules/inquiry/match_web.py",
    "app/modules/inquiry/web_helpers.py",
    "app/modules/products/catalog_web.py",
    "app/modules/products/media_web.py",
    "app/modules/products/pricing_web.py",
    "app/modules/products/records_web.py",
    "app/modules/products/sync_domain.py",
    "app/modules/products/sync_infrastructure.py",
    "app/modules/products/sync_repository.py",
    "app/modules/products/sync_service.py",
    "app/modules/products/sync_web.py",
    "app/modules/shipping/recognition_service.py",
    "app/modules/shipping/recognition_web.py",
    "app/modules/shipping/recognition_worker.py",
    "scripts/cleanup_runtime.py",
    "scripts/run_worker.py",
    "scripts/runtime_probe.py",
    "scripts/worker_health.py",
)


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 BLD 项目统一验收。")
    parser.add_argument("--quick", action="store_true", help="跳过完整单元测试，保留合同、依赖、静态和协议检查。")
    parser.add_argument("--base-ref", help="传给项目合同检查器的 Git 基准引用。")
    args = parser.parse_args()

    contract = [sys.executable, "scripts/check_project_contract.py"]
    if args.base_ref:
        contract.extend(["--base-ref", args.base_ref])
    run(contract)
    if shutil.which("uv"):
        run(["uv", "lock", "--check"])
    run([sys.executable, "-m", "ruff", "check", "app", "scripts", "tests", "tools", "app.py", "wsgi.py"])
    run([sys.executable, "-m", "pyright", *TYPECHECK_PATHS])
    run([sys.executable, "-m", "compileall", "-q", "app", "tools", "scripts", "app.py", "wsgi.py"])
    run([sys.executable, "scripts/route_snapshot.py", "--check"])
    run([sys.executable, "scripts/openapi_snapshot.py", "--check"])
    if not args.quick:
        run([sys.executable, "-m", "unittest", "discover", "-v"])
    print("verification: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
