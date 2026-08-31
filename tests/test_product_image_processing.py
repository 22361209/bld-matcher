from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.product_image_migration import migrate_product_images
from app.product_image_processing import (
    PRODUCT_IMAGE_LARGE_MAX_BYTES,
    PRODUCT_IMAGE_THUMB_MAX_BYTES,
    process_product_image,
    process_synced_product_image,
)


class ProductImageProcessingTests(unittest.TestCase):
    def test_large_photo_is_webp_with_hard_size_and_dimension_caps(self) -> None:
        source = io.BytesIO()
        Image.effect_noise((2400, 1800), 100).convert("RGB").save(source, format="PNG")

        processed = process_product_image(source)

        self.assertLessEqual(len(processed.large), PRODUCT_IMAGE_LARGE_MAX_BYTES)
        self.assertLessEqual(max(processed.large_size), 1920)
        self.assertLessEqual(len(processed.thumbnail), PRODUCT_IMAGE_THUMB_MAX_BYTES)
        self.assertLessEqual(processed.thumbnail_size[0], 320)
        self.assertLessEqual(processed.thumbnail_size[1], 240)
        with Image.open(io.BytesIO(processed.large)) as large:
            self.assertEqual(large.format, "WEBP")
        with Image.open(io.BytesIO(processed.thumbnail)) as thumbnail:
            self.assertEqual(thumbnail.format, "WEBP")

    def test_transparent_png_keeps_alpha_when_converted(self) -> None:
        source = io.BytesIO()
        image = Image.new("RGBA", (120, 80), (255, 0, 0, 0))
        image.putpixel((60, 40), (0, 120, 255, 255))
        image.save(source, format="PNG")

        processed = process_product_image(source)

        with Image.open(io.BytesIO(processed.large)) as large:
            self.assertEqual(large.mode, "RGBA")
            self.assertEqual(large.getchannel("A").getextrema(), (0, 255))

    def test_synced_webp_keeps_large_bytes_and_regenerates_thumbnail(self) -> None:
        source = io.BytesIO()
        Image.new("RGB", (960, 720), "teal").save(source, format="WEBP", quality=82)
        payload = source.getvalue()

        processed = process_synced_product_image(payload)

        self.assertEqual(processed.large, payload)
        self.assertLessEqual(processed.thumbnail_size[0], 320)
        self.assertLessEqual(processed.thumbnail_size[1], 240)
        with Image.open(io.BytesIO(processed.thumbnail)) as thumbnail:
            self.assertEqual(thumbnail.format, "WEBP")

    def test_synced_product_image_rejects_non_webp_payload(self) -> None:
        source = io.BytesIO()
        Image.new("RGB", (320, 240), "navy").save(source, format="PNG")

        with self.assertRaisesRegex(ValueError, "必须是 WebP"):
            process_synced_product_image(source.getvalue())


class ProductImageMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "products.sqlite3"
        self.images = self.root / "product_images"
        self.thumbs = self.images / "thumbs"
        self.images.mkdir()
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            CREATE TABLE products (
              id INTEGER PRIMARY KEY,
              bld_no TEXT NOT NULL,
              image_path TEXT DEFAULT '',
              image_path_2 TEXT DEFAULT '',
              image_path_3 TEXT DEFAULT '',
              image_path_4 TEXT DEFAULT '',
              image_path_5 TEXT DEFAULT ''
            )
            """
        )
        connection.execute(
            "INSERT INTO products (id, bld_no, image_path) VALUES (1, 'K-MIGRATE-001', 'data_product_images/K-MIGRATE-001.jpg')"
        )
        connection.commit()
        connection.close()
        Image.new("RGB", (2200, 1400), "navy").save(
            self.images / "K-MIGRATE-001.jpg",
            format="JPEG",
            quality=98,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_migration_converts_relinks_removes_source_and_is_idempotent(self) -> None:
        report = migrate_product_images(
            self.database,
            self.images,
            self.thumbs,
            apply=True,
        )

        target = self.images / "K-MIGRATE-001.webp"
        thumbnail = self.thumbs / "K-MIGRATE-001.webp"
        self.assertEqual(report["converted"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertFalse((self.images / "K-MIGRATE-001.jpg").exists())
        self.assertTrue(target.exists())
        self.assertTrue(thumbnail.exists())
        self.assertLessEqual(target.stat().st_size, PRODUCT_IMAGE_LARGE_MAX_BYTES)
        connection = sqlite3.connect(self.database)
        try:
            reference = connection.execute("SELECT image_path FROM products WHERE id = 1").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(reference, "data_product_images/K-MIGRATE-001.webp")

        check = migrate_product_images(
            self.database,
            self.images,
            self.thumbs,
            apply=False,
        )
        self.assertEqual(check["compliant"], 1)
        self.assertEqual(check["needs_conversion"], 0)


if __name__ == "__main__":
    unittest.main()
