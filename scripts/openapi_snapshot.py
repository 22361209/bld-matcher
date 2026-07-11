from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "contracts" / "openapi-v1.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _document() -> dict:
    with tempfile.TemporaryDirectory(prefix="bld-openapi-contract-") as temporary_dir:
        root = Path(temporary_dir)
        environment = {
            "SECRET_KEY": "openapi-contract-secret",
            "DEFAULT_ADMIN_PASSWORD": "openapi-contract-admin",
            "BLD_DATA_DIR": str(root / "data"),
            "BLD_UPLOAD_DIR": str(root / "uploads"),
            "BLD_OUTPUT_DIR": str(root / "outputs"),
            "INTERNAL_API_TOKEN": "",
        }
        previous = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)
        try:
            for module_name in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
                sys.modules.pop(module_name, None)
            spec = spec_from_file_location("bld_openapi_contract_app", ROOT / "app.py")
            if spec is None or spec.loader is None:
                raise RuntimeError("Unable to load app.py for OpenAPI generation.")
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            from app.platform.openapi import build_openapi_document

            return build_openapi_document()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _serialized() -> str:
    return json.dumps(_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或更新 API v1 OpenAPI 合同快照。")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="验证当前合同与提交快照一致。")
    action.add_argument("--write", action="store_true", help="将当前合同写入提交快照。")
    args = parser.parse_args()
    current = _serialized()
    if args.write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(current, encoding="utf-8")
        print(f"OpenAPI snapshot updated: {SNAPSHOT_PATH.relative_to(ROOT)}")
        return 0
    if not SNAPSHOT_PATH.is_file():
        print(f"ERROR: OpenAPI snapshot is missing: {SNAPSHOT_PATH.relative_to(ROOT)}")
        return 1
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    if current != expected:
        print("ERROR: OpenAPI v1 contract drifted; review compatibility and run --write intentionally.")
        return 1
    print("OpenAPI snapshot: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
