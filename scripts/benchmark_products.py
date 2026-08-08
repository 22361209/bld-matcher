from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
import tempfile
import time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()


def _load_app(runtime_root: Path):
    environment = {
        "SECRET_KEY": "product-route-benchmark-only-secret",
        "DEFAULT_ADMIN_PASSWORD": "product-route-benchmark-unused-password",
        "BLD_DATA_DIR": str(runtime_root / "data"),
        "BLD_UPLOAD_DIR": str(runtime_root / "uploads"),
        "BLD_OUTPUT_DIR": str(runtime_root / "outputs"),
        "INTERNAL_API_TOKEN": "",
        "APP_DEBUG": "0",
    }
    os.environ.update(environment)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for module_name in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
        sys.modules.pop(module_name, None)
    spec = spec_from_file_location("bld_product_route_benchmark_app", ROOT / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for the product route benchmark.")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.app.config["TESTING"] = True
    return module


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def _measure(client, path: str, *, warmup: int, rounds: int) -> dict[str, object]:
    for _ in range(warmup):
        response = client.get(path)
        response.get_data()
        if response.status_code != 200:
            raise RuntimeError(f"Warmup request {path} returned HTTP {response.status_code}.")

    samples: list[float] = []
    response_bytes = 0
    for _ in range(rounds):
        started = time.perf_counter()
        response = client.get(path)
        body = response.get_data()
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise RuntimeError(f"Measured request {path} returned HTTP {response.status_code}.")
        response_bytes = len(body)
        samples.append(elapsed_ms)
    return {
        "path": path,
        "rounds": rounds,
        "response_bytes": response_bytes,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark authenticated full-page /products rendering against an isolated SQLite snapshot."
    )
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "products.sqlite3")
    parser.add_argument("--username", default="007")
    parser.add_argument("--query", default="HONDA")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--catalog-p95-ms", type=float, default=70.0)
    parser.add_argument("--search-p95-ms", type=float, default=20.0)
    args = parser.parse_args(argv)

    database_path = args.database.expanduser().resolve()
    if not database_path.is_file():
        parser.error(f"database does not exist: {database_path}")
    if args.rounds < 5:
        parser.error("--rounds must be at least 5")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")

    with tempfile.TemporaryDirectory(prefix="bld-products-benchmark-") as temporary_dir:
        runtime_root = Path(temporary_dir)
        runtime_database = runtime_root / "data" / "products.sqlite3"
        _snapshot_database(database_path, runtime_database)
        snapshot_hash = _sha256(runtime_database)
        module = _load_app(runtime_root)
        with sqlite3.connect(runtime_database) as connection:
            user = connection.execute(
                "SELECT id FROM users WHERE username = ? AND active = 1",
                (args.username,),
            ).fetchone()
            product_count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
        if user is None:
            raise RuntimeError(f"Enabled benchmark user {args.username!r} does not exist.")

        client = module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = int(user[0])

        catalog = _measure(client, "/products", warmup=args.warmup, rounds=args.rounds)
        search_path = f"/products?{urlencode({'q': args.query})}"
        search = _measure(client, search_path, warmup=args.warmup, rounds=args.rounds)

    checks = {
        "catalog_p95": {
            "actual_ms": catalog["p95_ms"],
            "limit_ms": args.catalog_p95_ms,
            "passed": float(catalog["p95_ms"]) <= args.catalog_p95_ms,
        },
        "search_p95": {
            "actual_ms": search["p95_ms"],
            "limit_ms": args.search_p95_ms,
            "passed": float(search["p95_ms"]) <= args.search_p95_ms,
        },
    }
    result = {
        "database": str(database_path),
        "database_snapshot_sha256": snapshot_hash,
        "product_count": product_count,
        "username": args.username,
        "method": "Flask test client; full HTML body; nearest-rank p95; isolated SQLite Backup API snapshot",
        "catalog": catalog,
        "search": search,
        "checks": checks,
        "passed": all(bool(check["passed"]) for check in checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
