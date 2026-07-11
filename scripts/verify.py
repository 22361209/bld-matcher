from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 BLD 项目统一验收。")
    parser.add_argument("--quick", action="store_true", help="只运行合同检查、依赖锁检查和语法检查。")
    parser.add_argument("--base-ref", help="传给项目合同检查器的 Git 基准引用。")
    args = parser.parse_args()

    contract = [sys.executable, "scripts/check_project_contract.py"]
    if args.base_ref:
        contract.extend(["--base-ref", args.base_ref])
    run(contract)
    if shutil.which("uv"):
        run(["uv", "lock", "--check"])
    run([sys.executable, "-m", "ruff", "check", "app", "scripts", "tests", "tools", "app.py", "wsgi.py"])
    run([sys.executable, "-m", "compileall", "-q", "app", "tools", "scripts", "app.py", "wsgi.py"])
    if not args.quick:
        run([sys.executable, "-m", "unittest", "discover", "-v"])
    print("verification: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
