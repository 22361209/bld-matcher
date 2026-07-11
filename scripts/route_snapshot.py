from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "contracts" / "routes.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _route_document() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="bld-route-contract-") as temporary_dir:
        root = Path(temporary_dir)
        environment = {
            "SECRET_KEY": "route-contract-secret",
            "DEFAULT_ADMIN_PASSWORD": "route-contract-admin",
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
            spec = spec_from_file_location("bld_route_contract_app", ROOT / "app.py")
            if spec is None or spec.loader is None:
                raise RuntimeError("Unable to load app.py for route contract generation.")
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            routes = [
                {
                    "endpoint": rule.endpoint,
                    "methods": sorted(set(rule.methods or ()) - {"HEAD", "OPTIONS"}),
                    "rule": str(rule),
                }
                for rule in module.app.url_map.iter_rules()
                if rule.endpoint != "static"
            ]
            routes.sort(key=lambda route: (route["rule"], route["methods"], route["endpoint"]))
            return {"version": 1, "routes": routes}
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _serialized() -> str:
    return json.dumps(_route_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or update the committed Flask route contract.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Compare runtime routes with the committed snapshot.")
    action.add_argument("--write", action="store_true", help="Write the current runtime routes to the snapshot.")
    args = parser.parse_args()
    current = _serialized()
    if args.write:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(current, encoding="utf-8")
        print(f"Route snapshot updated: {SNAPSHOT_PATH.relative_to(ROOT)}")
        return 0
    if not SNAPSHOT_PATH.is_file():
        print(f"ERROR: Route snapshot is missing: {SNAPSHOT_PATH.relative_to(ROOT)}")
        return 1
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    if current != expected:
        print("ERROR: Flask route contract drifted; review compatibility and run --write intentionally.")
        return 1
    print("Route snapshot: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
