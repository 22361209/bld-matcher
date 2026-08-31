#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DB_PATH, PRODUCT_IMAGE_DIR, PRODUCT_IMAGE_MIGRATION_MARKER, PRODUCT_IMAGE_THUMB_DIR  # noqa: E402
from app.product_image_migration import migrate_product_images  # noqa: E402
from app.product_image_processing import atomic_write_bytes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check or migrate active product images to bounded WebP files and WebP thumbnails."
    )
    parser.add_argument("--apply", action="store_true", help="Convert images, update references, and remove replaced source files.")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Return success after writing the report even when individual images failed.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DB_PATH.parent / "product_image_migration_report.json",
        help="JSON report path.",
    )
    args = parser.parse_args()
    if args.apply:
        atomic_write_bytes(PRODUCT_IMAGE_MIGRATION_MARKER, b"product image migration in progress\n")
    try:
        report = migrate_product_images(
            DB_PATH,
            PRODUCT_IMAGE_DIR,
            PRODUCT_IMAGE_THUMB_DIR,
            apply=args.apply,
            report_path=args.report,
        )
    finally:
        if args.apply:
            PRODUCT_IMAGE_MIGRATION_MARKER.unlink(missing_ok=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    incomplete = bool(report.get("missing") or report.get("failed") or (not args.apply and report.get("needs_conversion")))
    return 0 if args.allow_failures or not incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
