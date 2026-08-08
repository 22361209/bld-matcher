from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import connect  # noqa: E402
from app.matcher import ProductCatalog  # noqa: E402
from app.modules.products.repository import SQLiteProductRepository  # noqa: E402

from benchmark_products import _sha256, _snapshot_database  # noqa: E402


BASELINE_P95_MS = {"hit_100": 3.855, "hit_75": 132.005, "hit_0": 577.572}


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _measure(catalog: ProductCatalog, queries: list[str], rounds: int) -> dict[str, object]:
    samples: list[float] = []
    matched = 0
    for _ in range(rounds):
        started = time.process_time()
        matched = sum(catalog.match("", query) is not None for query in queries)
        samples.append((time.process_time() - started) * 1000)
    return {
        "queries": len(queries),
        "matched": matched,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the OE prefix index on a deterministic workload.")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "products.sqlite3")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--queries", type=int, default=1_000)
    args = parser.parse_args(argv)
    if args.rounds < 5:
        parser.error("--rounds must be at least 5")
    if args.queries < 1_000:
        parser.error("--queries must be at least 1000")

    database_path = args.database.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="bld-matcher-prefix-benchmark-") as temporary_dir:
        snapshot_path = Path(temporary_dir) / "products.sqlite3"
        _snapshot_database(database_path, snapshot_path)
        snapshot_hash = _sha256(snapshot_path)
        with connect(snapshot_path) as connection:
            repository = SQLiteProductRepository(connection, snapshot_path)
            _version, rows, aliases = repository.catalog_snapshot()
        catalog = ProductCatalog(rows, manual_map=aliases)

    products = [str(row.get("BLD NO.") or "").strip() for row in catalog.rows]
    products = [value for value in products if value]
    if not products:
        raise RuntimeError("Catalog has no BLD numbers.")

    def workload(matched_rows: int, seed: int) -> list[str]:
        flags = [True] * matched_rows + [False] * (args.queries - matched_rows)
        random.Random(seed).shuffle(flags)
        return [
            products[index % len(products)] if is_match else f"NO-MATCH-{index:05d}"
            for index, is_match in enumerate(flags)
        ]

    workloads = {
        "hit_100": workload(args.queries, 100),
        "hit_75": workload(int(args.queries * 0.75), 75),
        "hit_0": workload(0, 0),
    }
    measurements = {
        label: _measure(catalog, queries, args.rounds)
        for label, queries in workloads.items()
    }
    gates = {
        "hit_100": float(measurements["hit_100"]["p95_ms"]) <= 5.0,
        "hit_75": float(measurements["hit_75"]["p95_ms"]) <= BASELINE_P95_MS["hit_75"] / 2.5,
        "hit_0": float(measurements["hit_0"]["p95_ms"]) <= BASELINE_P95_MS["hit_0"] / 5.0,
    }
    result = {
        "database": str(database_path),
        "database_snapshot_sha256": snapshot_hash,
        "catalog_products": len(catalog.rows),
        "oe_keys": len(catalog.by_oe),
        "rounds": args.rounds,
        "baseline_cpu_p95_ms": BASELINE_P95_MS,
        "measurements": measurements,
        "gates": gates,
        "passed": all(gates.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
