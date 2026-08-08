from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import sys
import tarfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import connect  # noqa: E402
from app.modules.business_sync.infrastructure import BusinessSyncRepository  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths(fixture_root: Path) -> tuple[Path, Path, Path]:
    return (
        fixture_root / "fixture.tar.gz",
        fixture_root / "target-template.sqlite3",
        fixture_root / "token.txt",
    )


def _prepare(fixture_root: Path) -> dict[str, object]:
    package, template_database, token_path = _paths(fixture_root)
    if any(path.exists() for path in (package, template_database, token_path)):
        raise RuntimeError("Fixture already exists; use a new temporary --fixture-root.")
    source_database = fixture_root / "source.sqlite3"
    drawing_dir = fixture_root / "source-drawings"
    image_dir = fixture_root / "source-images"
    material_dir = fixture_root / "source-material-drawings"
    for directory in (drawing_dir, image_dir, material_dir):
        directory.mkdir(parents=True, exist_ok=True)

    with connect(source_database) as connection:
        connection.execute(
            "INSERT INTO products (bld_no, created_at, updated_at) VALUES (?, ?, ?)",
            ("PERF-PRODUCT", "2026-08-08 00:00:00", "2026-08-08 00:00:00"),
        )
        connection.execute(
            """
            INSERT INTO material_items
              (sync_id, model, code, pieces, thickness, width, length, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "perf-material",
                "PERF-MODEL",
                "PERF-CODE",
                1,
                2,
                3,
                4,
                "2026-08-08 00:00:00",
                "2026-08-08 00:00:00",
            ),
        )
        connection.commit()

    generator = random.Random(20260808)
    for index in range(765):
        (drawing_dir / f"DRAW-{index:04d}.pdf").write_bytes(generator.randbytes(23_000))
        (image_dir / f"IMAGE-{index:04d}.webp").write_bytes(generator.randbytes(23_000))
        (material_dir / f"MAT-{index:04d}.pdf").write_bytes(generator.randbytes(23_000))

    source_repository = BusinessSyncRepository(
        source_database,
        drawing_dir=drawing_dir,
        image_dir=image_dir,
        material_drawing_dir=material_dir,
    )
    source_repository.export(
        output_path=package,
        selected=("products", "materials"),
        actor="performance-fixture",
        include_drawings=True,
        include_images=True,
        include_material_drawings=True,
    )
    with connect(template_database):
        pass
    target_repository = BusinessSyncRepository(
        template_database,
        drawing_dir=fixture_root / "unused-drawings",
        image_dir=fixture_root / "unused-images",
        material_drawing_dir=fixture_root / "unused-materials",
    )
    token_path.write_text(str(target_repository.preview(package)["token"]), encoding="utf-8")
    with tarfile.open(package, "r:gz") as archive:
        member_count = len(archive.getmembers())
    return {
        "package_bytes": package.stat().st_size,
        "package_sha256": _sha256(package),
        "member_count": member_count,
        "media_files": 2_295,
        "generator_seed": 20260808,
    }


def _run(
    fixture_root: Path,
    *,
    rounds: int,
    without_media: bool,
    p95_limit_ms: float,
) -> dict[str, object]:
    package, template_database, token_path = _paths(fixture_root)
    for path in (package, template_database, token_path):
        if not path.exists():
            raise RuntimeError(f"Missing prepared fixture: {path}")
    token = token_path.read_text(encoding="utf-8")
    samples: list[float] = []
    archive_opens: list[int] = []

    for index in range(rounds):
        scenario = "no-media" if without_media else "media"
        round_root = fixture_root / f"run-{scenario}-{index:02d}"
        round_root.mkdir()
        target_database = round_root / "target.sqlite3"
        shutil.copy2(template_database, target_database)
        drawing_dir = round_root / "drawings"
        image_dir = round_root / "images"
        material_dir = round_root / "materials"
        repository = BusinessSyncRepository(
            target_database,
            drawing_dir=drawing_dir,
            image_dir=image_dir,
            material_drawing_dir=material_dir,
        )
        original_open = tarfile.open
        modes: list[str] = []

        def counted_open(*open_args, **open_kwargs):
            mode = open_args[1] if len(open_args) > 1 else open_kwargs.get("mode", "r")
            modes.append(str(mode))
            return original_open(*open_args, **open_kwargs)

        tarfile.open = counted_open
        started = time.perf_counter()
        try:
            repository.apply(
                package,
                backup_path=round_root / "backup.sqlite3",
                actor="performance-benchmark",
                expected_token=token,
                selected_conflicts={},
                include_drawings=not without_media,
                include_images=not without_media,
                include_material_drawings=not without_media,
            )
        finally:
            samples.append((time.perf_counter() - started) * 1000)
            tarfile.open = original_open
        archive_opens.append(sum(mode == "r:gz" for mode in modes))
        if not without_media:
            assert len(list(drawing_dir.iterdir())) == 765
            assert len(list(image_dir.iterdir())) == 765
            assert len(list(material_dir.iterdir())) == 765
        shutil.rmtree(round_root)

    p95_ms = _percentile(samples, 0.95)
    checks = {
        "p95": p95_ms <= p95_limit_ms,
        "archive_read_opens": all(count <= 2 for count in archive_opens),
    }
    return {
        "rounds": rounds,
        "package_bytes": package.stat().st_size,
        "package_sha256": _sha256(package),
        "without_media": without_media,
        "samples_ms": [round(value, 3) for value in samples],
        "p50_ms": round(_percentile(samples, 0.50), 3),
        "p95_ms": round(p95_ms, 3),
        "p95_limit_ms": p95_limit_ms,
        "archive_read_opens": archive_opens,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and benchmark an isolated deterministic business-sync media package."
    )
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--without-media", action="store_true")
    parser.add_argument("--p95-ms", type=float)
    args = parser.parse_args(argv)
    fixture_root = args.fixture_root.expanduser().resolve()
    fixture_root.mkdir(parents=True, exist_ok=True)
    if args.prepare:
        result = _prepare(fixture_root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.rounds < 5:
        parser.error("--rounds must be at least 5")
    default_limit = 190.0 if args.without_media else 1_080.0
    result = _run(
        fixture_root,
        rounds=args.rounds,
        without_media=args.without_media,
        p95_limit_ms=args.p95_ms if args.p95_ms is not None else default_limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
