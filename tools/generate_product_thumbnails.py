#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import PRODUCT_IMAGE_DIR, PRODUCT_IMAGE_THUMB_DIR
from app.product_media import IMAGE_SUFFIXES, generate_product_image_thumb, product_image_thumb_path


def iter_product_images() -> list[Path]:
    if not PRODUCT_IMAGE_DIR.exists():
        return []
    return sorted(
        path
        for path in PRODUCT_IMAGE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate small product thumbnails for the catalog table.")
    parser.add_argument("--force", action="store_true", help="Regenerate existing thumbnails.")
    args = parser.parse_args()

    PRODUCT_IMAGE_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    created = 0
    skipped = 0
    failed = 0
    for source in iter_product_images():
        destination = product_image_thumb_path(source.name)
        if not args.force and destination and destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
            skipped += 1
            continue
        if generate_product_image_thumb(source):
            created += 1
        else:
            failed += 1

    print(f"images: {created + skipped + failed}")
    print(f"generated: {created}")
    print(f"skipped: {skipped}")
    print(f"failed: {failed}")
    print(f"thumb_dir: {PRODUCT_IMAGE_THUMB_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
