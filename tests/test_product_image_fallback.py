from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import helpers


class DataDirProductImageFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.image_dir = Path(self.temporary.name)
        self.app = Flask(__name__)
        self.app.add_url_rule("/product-images/<path:name>", endpoint="product_image_data")
        self.app.add_url_rule("/product-image-thumbs/<path:name>", endpoint="product_image_thumb_data")
        helpers.clear_product_image_caches()

    def tearDown(self) -> None:
        helpers.clear_product_image_caches()
        self.temporary.cleanup()

    def _urls(self, product: dict, slot: int = 1) -> tuple[str, str]:
        with patch.object(helpers, "PRODUCT_IMAGE_DIR", self.image_dir):
            helpers.clear_product_image_caches()
            with self.app.test_request_context():
                return (
                    helpers.product_image_url(product, slot),
                    helpers.product_image_thumb_url(product, slot),
                )

    def test_empty_image_path_falls_back_to_synced_data_dir_file(self) -> None:
        (self.image_dir / "K6001B_1.png").write_bytes(b"png")
        url, thumb = self._urls({"bld_no": "K6001B", "image_path": ""})
        self.assertEqual(url, "/product-images/K6001B_1.png")
        self.assertEqual(thumb, "/product-image-thumbs/K6001B_1.png")

    def test_data_dir_fallback_supports_extra_slots(self) -> None:
        (self.image_dir / "K6001B_1.png").write_bytes(b"png")
        (self.image_dir / "K6001B_2.webp").write_bytes(b"webp")
        self.assertEqual(self._urls({"bld_no": "K6001B"}, slot=2)[0], "/product-images/K6001B_2.webp")
        self.assertEqual(self._urls({"bld_no": "K6001B"}, slot=3), ("", ""))

    def test_explicit_image_path_still_wins_over_data_dir_file(self) -> None:
        (self.image_dir / "K6001B_1.png").write_bytes(b"png")
        url, _ = self._urls({"bld_no": "K6001B", "image_path": "product_images/legacy.jpg"})
        self.assertEqual(url, "/static/product_images/legacy.jpg")

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(self._urls({"bld_no": "K-NONE", "image_path": ""}), ("", ""))

    def test_cache_clear_picks_up_files_added_after_a_previous_probe(self) -> None:
        self.assertEqual(self._urls({"bld_no": "K6001B"})[0], "")
        (self.image_dir / "K6001B_1.png").write_bytes(b"png")
        self.assertEqual(self._urls({"bld_no": "K6001B"})[0], "/product-images/K6001B_1.png")

    def test_legacy_unnumbered_and_dash_slot_names_remain_readable(self) -> None:
        (self.image_dir / "K6001B.png").write_bytes(b"png")
        (self.image_dir / "K6001B-2.webp").write_bytes(b"webp")
        self.assertEqual(self._urls({"bld_no": "K6001B"})[0], "/product-images/K6001B.png")
        self.assertEqual(self._urls({"bld_no": "K6001B"}, slot=2)[0], "/product-images/K6001B-2.webp")


class BusinessSyncImageCacheClearTest(unittest.TestCase):
    def _copy(self, requests: dict[str, bool]) -> unittest.mock.Mock:
        from app.modules.business_sync import infrastructure

        repository = infrastructure.BusinessSyncRepository(Path("/tmp/unused.sqlite3"))
        with (
            patch.object(infrastructure, "copy_requested_media") as copy_mock,
            patch.object(infrastructure, "clear_product_image_caches") as clear_mock,
        ):
            repository._copy_requested_media(
                Path("/tmp/package.tar.gz"),
                {},
                requests,
                Path("/tmp/backup"),
                [],
            )
        self.assertTrue(copy_mock.called)
        return clear_mock

    def test_product_image_sync_clears_probe_caches(self) -> None:
        self.assertTrue(self._copy({"product_images": True, "drawings": False}).called)

    def test_sync_without_product_images_keeps_probe_caches(self) -> None:
        self.assertFalse(self._copy({"product_images": False, "drawings": True}).called)


if __name__ == "__main__":
    unittest.main()
