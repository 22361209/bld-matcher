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
from html.parser import HTMLParser
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StartTagCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs
        self.count += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs
        self.count += 1


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
    os.environ.update(
        {
            "SECRET_KEY": "quote-route-benchmark-only-secret",
            "DEFAULT_ADMIN_PASSWORD": "quote-route-benchmark-unused-password",
            "BLD_DATA_DIR": str(runtime_root / "data"),
            "BLD_UPLOAD_DIR": str(runtime_root / "uploads"),
            "BLD_OUTPUT_DIR": str(runtime_root / "outputs"),
            "INTERNAL_API_TOKEN": "",
            "APP_DEBUG": "0",
        }
    )
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for module_name in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
        sys.modules.pop(module_name, None)
    spec = spec_from_file_location("bld_quote_route_benchmark_app", ROOT / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for the quote route benchmark.")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.app.config["TESTING"] = True
    return module


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark authenticated /quotes rendering against an isolated SQLite snapshot."
    )
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "products.sqlite3")
    parser.add_argument("--username", default="007")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--html-bytes", type=int, default=220_000)
    parser.add_argument("--start-tags", type=int, default=4_714)
    parser.add_argument("--p95-ms", type=float, default=13.942)
    args = parser.parse_args(argv)
    if args.rounds < 5:
        parser.error("--rounds must be at least 5")

    database_path = args.database.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="bld-quotes-benchmark-") as temporary_dir:
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
            quote_count = int(connection.execute("SELECT COUNT(*) FROM quote_records").fetchone()[0])
        if user is None:
            raise RuntimeError(f"Enabled benchmark user {args.username!r} does not exist.")

        client = module.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = int(user[0])
        for _ in range(args.warmup):
            response = client.get("/quotes")
            response.get_data()
            if response.status_code != 200:
                raise RuntimeError(f"Warmup returned HTTP {response.status_code}.")

        samples: list[float] = []
        body = b""
        for _ in range(args.rounds):
            started = time.perf_counter()
            response = client.get("/quotes")
            body = response.get_data()
            samples.append((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                raise RuntimeError(f"Measured request returned HTTP {response.status_code}.")

    html = body.decode("utf-8")
    counter = StartTagCounter()
    counter.feed(html)
    checks = {
        "html_bytes": len(body) <= args.html_bytes,
        "start_tags": counter.count <= args.start_tags,
        "p95_ms": _percentile(samples, 0.95) <= args.p95_ms,
        "single_edit_dialog": html.count('class="quote-edit-dialog"') == 1,
        "single_edit_form": html.count("data-quote-edit-form") == 1,
        "lazy_filter_shells": html.count("data-quote-filter-options></div>") == 11,
    }
    result = {
        "database": str(database_path),
        "database_snapshot_sha256": snapshot_hash,
        "quote_count": quote_count,
        "rounds": args.rounds,
        "method": "Flask test client; full HTML body; nearest-rank p95; isolated SQLite Backup API snapshot",
        "response_bytes": len(body),
        "start_tag_count": counter.count,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(_percentile(samples, 0.95), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
