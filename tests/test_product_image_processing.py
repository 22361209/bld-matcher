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
from app.product_media import product_image_storage_name


class ProductImageProcessingTests(unittest.TestCase):
    def test_product_image_slots_use_underscore_numbering(self) -> None:
        self.assertEqual(
            [product_image_storage_name("K8080LA-2", ".WEBP", slot) for slot in range(1, 6)],
            [
                "K8080LA-2_1.webp",
                "K8080LA-2_2.webp",
                "K8080LA-2_3.webp",
                "K8080LA-2_4.webp",
                "K8080LA-2_5.webp",
            ],
        )

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

        target = self.images / "K-MIGRATE-001_1.webp"
        thumbnail = self.thumbs / "K-MIGRATE-001_1.webp"
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
        self.assertEqual(reference, "data_product_images/K-MIGRATE-001_1.webp")

        check = migrate_product_images(
            self.database,
            self.images,
            self.thumbs,
            apply=False,
        )
        self.assertEqual(check["compliant"], 1)
        self.assertEqual(check["needs_conversion"], 0)

    def test_migration_renames_compliant_webp_without_recompressing_large_image(self) -> None:
        source = self.images / "K-MIGRATE-001.webp"
        buffer = io.BytesIO()
        Image.new("RGB", (960, 720), "teal").save(buffer, format="WEBP", quality=82)
        payload = buffer.getvalue()
        source.write_bytes(payload)
        (self.images / "K-MIGRATE-001.jpg").unlink()
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE products SET image_path = ? WHERE id = 1",
            ("data_product_images/K-MIGRATE-001.webp",),
        )
        connection.commit()
        connection.close()

        report = migrate_product_images(self.database, self.images, self.thumbs, apply=True)

        target = self.images / "K-MIGRATE-001_1.webp"
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(source.exists())
        self.assertTrue((self.thumbs / target.name).is_file())
        self.assertEqual(report["failed"], 0)

    def test_migration_splits_shared_dash_name_into_distinct_underscore_slots(self) -> None:
        shared_source = self.images / "K8080LA-2.webp"
        buffer = io.BytesIO()
        Image.new("RGB", (960, 720), "navy").save(buffer, format="WEBP", quality=82)
        payload = buffer.getvalue()
        shared_source.write_bytes(payload)
        (self.images / "K-MIGRATE-001.jpg").unlink()
        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM products")
        connection.execute(
            "INSERT INTO products (id, bld_no, image_path_2) VALUES (1, 'K8080LA', ?)",
            ("data_product_images/K8080LA-2.webp",),
        )
        connection.execute(
            "INSERT INTO products (id, bld_no, image_path) VALUES (2, 'K8080LA-2', ?)",
            ("data_product_images/K8080LA-2.webp",),
        )
        connection.commit()
        connection.close()

        report = migrate_product_images(self.database, self.images, self.thumbs, apply=True)

        first_target = self.images / "K8080LA_2.webp"
        second_target = self.images / "K8080LA-2_1.webp"
        self.assertEqual(first_target.read_bytes(), payload)
        self.assertEqual(second_target.read_bytes(), payload)
        self.assertFalse(shared_source.exists())
        self.assertEqual(report["removed_source_files"], 1)
        self.assertEqual(report["failed"], 0)
        connection = sqlite3.connect(self.database)
        references = connection.execute(
            "SELECT bld_no, image_path, image_path_2 FROM products ORDER BY id"
        ).fetchall()
        connection.close()
        self.assertEqual(references[0][2], "data_product_images/K8080LA_2.webp")
        self.assertEqual(references[1][1], "data_product_images/K8080LA-2_1.webp")


if __name__ == "__main__":
    unittest.main()
