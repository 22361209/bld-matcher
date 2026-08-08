from __future__ import annotations

from tests.web_app_test_base import (
    WebAppTestBase,
    PROJECT_ROOT,
    io,
    patch,
)


class TestWebProductMedia(WebAppTestBase):
    def test_product_drawing_upload_preview_and_batch_entry(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-DRAW-001",
                    "series": "TEST",
                    "item": "DRAWING PART",
                    "oe_no_1": "DRAW-001",
                    "models": "Tester",
                    "active": "1",
                },
                actor="tester",
            )
            product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-001",)).fetchone()

        self.login()
        response = self.client.get("/products", query_string={"bld": "K-DRAW-001"})
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-open-edit-product-modal", html)
        self.assertIn("data-bld-action-menu", html)
        self.assertIn("data-product-drawing-unavailable", html)
        self.assertIn("data-copy-product", html)
        self.assertIn('name="product_image_1"', html)
        self.assertIn("data-product-media-batch-input", html)
        self.assertIn("multiple", html)
        self.assertIn("拖入图片或选择文件", html)
        self.assertIn("product-media-tile-header", html)
        self.assertIn("product-media-tile-label", html)
        self.assertIn("data-product-media-drawing-intake", html)
        self.assertNotIn("data-product-media-replace", html)
        self.assertNotIn("file-picker-clear", html)
        self.assertIn('name="drawing"', html)
        self.assertIn(
            '{% include "_product_media_fields.html" %}',
            (PROJECT_ROOT / "templates" / "products.html").read_text(encoding="utf-8"),
        )
        self.assertIn(
            '{% include "_product_media_fields.html" %}',
            (PROJECT_ROOT / "templates" / "product_form.html").read_text(encoding="utf-8"),
        )
        self.assertIn("data-drawing-unavailable-modal", html)
        products_js = (PROJECT_ROOT / "static" / "pages" / "products.js").read_text(encoding="utf-8")
        self.assertIn("openDrawingUnavailableModal(drawingUnavailable.dataset.productBldNo);", products_js)
        self.assertNotIn("window.alert(", products_js)
        products_css = (PROJECT_ROOT / "static" / "pages" / "products.css").read_text(encoding="utf-8")
        self.assertIn(
            ".product-create-modal-panel .product-media-edit,\n.product-create-modal-panel .file-picker-control",
            products_css,
        )
        self.assertNotIn("PDF图纸", html)
        self.assertIn("批量上传图纸", html)
        self.assertNotIn(f'href="/products/{product["id"]}/drawing"', html)

        edit = self.client.get(f"/products/{product['id']}/edit")
        edit_html = edit.get_data(as_text=True)
        self.assertEqual(edit.status_code, 200)
        for slot in range(1, 6):
            self.assertIn(f'name="product_image_{slot}"', edit_html)
        self.assertIn("data-product-media-upload", edit_html)
        self.assertIn("data-product-media-browse", edit_html)
        self.assertIn("data-product-media-drawing-intake", edit_html)
        self.assertNotIn("当前没有 PDF 图纸", edit_html)
        self.assertNotIn("file-picker-clear", edit_html)
        self.assertIn("/static/app.js", edit_html)
        self.assertIn('name="drawing"', edit_html)
        media_css = (PROJECT_ROOT / "static" / "pages" / "product_media_uploader.css").read_text(encoding="utf-8")
        self.assertIn("--product-media-card-width: 280px", media_css)
        self.assertIn("--product-media-card-height: 178px", media_css)
        self.assertIn("grid-template-columns: repeat(2, var(--product-media-card-width))", media_css)
        self.assertIn("block-size: var(--product-media-card-height)", media_css)
        self.assertIn(".product-media-drawing-intake.has-media", media_css)
        self.assertIn("max-inline-size: 100%", media_css)

        embedded = self.client.get(f"/products/{product['id']}/edit", query_string={"embedded": "1"})
        embedded_html = embedded.get_data(as_text=True)
        self.assertEqual(embedded.status_code, 200)
        self.assertIn("embedded-product-form-page", embedded_html)
        self.assertIn('name="embedded" value="1"', embedded_html)
        self.assertNotIn("返回目录", embedded_html)

        embedded_save = self.client.post(
            "/products/save",
            data={
                "embedded": "1",
                "bld_no": "K-DRAW-001",
                "series": "TEST",
                "item": "DRAWING PART",
                "oe_no_1": "DRAW-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "active": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(embedded_save.status_code, 200)
        embedded_save_html = embedded_save.get_data(as_text=True)
        self.assertIn("window.parent.postMessage", embedded_save_html)
        self.assertIn('"type": "bld:product-mutated"', embedded_save_html)
        self.assertNotIn("window.parent.location.reload()", embedded_save_html)

        upload = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-001",
                "series": "TEST",
                "item": "DRAWING PART",
                "oe_no_1": "DRAW-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "active": "1",
                "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\nproduct image 1"), "K-DRAW-001.png"),
                "product_image_2": (io.BytesIO(b"\x89PNG\r\n\x1a\nproduct image 2"), "K-DRAW-001-2.png"),
                "drawing": (io.BytesIO(b"%PDF-1.4\nfirst drawing\n%%EOF"), "K-DRAW-001.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(upload.status_code, 302)

        with self.web.connect(self.web.DB_PATH) as conn:
            updated = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-001",)).fetchone()
        drawing_path = self.root / "data" / updated["drawing_path"]
        image_path = self.root / "data" / "product_images" / "K-DRAW-001.png"
        image_path_2 = self.root / "data" / "product_images" / "K-DRAW-001-2.png"
        self.assertTrue(drawing_path.exists())
        self.assertTrue(image_path.exists())
        self.assertTrue(image_path_2.exists())
        self.assertEqual(updated["drawing_original_name"], "K-DRAW-001.pdf")
        self.assertEqual(updated["image_path"], "data_product_images/K-DRAW-001.png")
        self.assertEqual(updated["image_path_2"], "data_product_images/K-DRAW-001-2.png")

        response = self.client.get("/products", query_string={"bld": "K-DRAW-001"})
        html = response.get_data(as_text=True)
        self.assertIn(f'href="/products/{product["id"]}/drawing"', html)
        self.assertNotIn("替换图纸", html)
        self.assertIn("/product-image-thumbs/K-DRAW-001.png", html)
        self.assertIn("/product-images/K-DRAW-001.png", html)
        self.assertIn("/product-images/K-DRAW-001-2.png", html)

        copied = self.client.post(
            "/products/save",
            data={
                "copy_source_product_id": str(product["id"]),
                "bld_no": "K-DRAW-COPY-001",
                "series": "TEST",
                "item": "DRAWING PART",
                "oe_no_1": "DRAW-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "product_status": "",
                "active": "1",
                "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\ncopied image"), "K-DRAW-COPY-001.png"),
                "drawing": (io.BytesIO(b"%PDF-1.4\ncopied drawing\n%%EOF"), "K-DRAW-COPY-001.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(copied.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            copied_product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-COPY-001",)).fetchone()
        self.assertIsNotNone(copied_product)
        self.assertEqual(copied_product["series"], "TEST")
        self.assertEqual(copied_product["item"], "DRAWING PART")
        self.assertEqual(copied_product["image_path"], "data_product_images/K-DRAW-COPY-001.png")
        self.assertEqual(copied_product["image_path_2"], "data_product_images/K-DRAW-COPY-001-2.png")
        self.assertEqual(copied_product["drawing_path"], "drawings/pdf/K-DRAW-COPY-001.pdf")
        self.assertTrue((self.root / "data" / "product_images" / "K-DRAW-COPY-001-2.png").exists())
        self.assertEqual(
            (self.root / "data" / "product_images" / "K-DRAW-COPY-001.png").read_bytes(),
            b"\x89PNG\r\n\x1a\ncopied image",
        )
        self.assertEqual(
            (self.root / "data" / "drawings" / "pdf" / "K-DRAW-COPY-001.pdf").read_bytes(),
            b"%PDF-1.4\ncopied drawing\n%%EOF",
        )

        duplicate_copy = self.client.post(
            "/products/save",
            data={"copy_source_product_id": str(product["id"]), "bld_no": "K-DRAW-COPY-001", "active": "1"},
            follow_redirects=False,
        )
        self.assertEqual(duplicate_copy.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            unchanged_copy = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-COPY-001",)).fetchone()
        self.assertEqual(unchanged_copy["item"], "DRAWING PART")

        image = self.client.get("/product-images/K-DRAW-001.png")
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.get_data().startswith(b"\x89PNG"))
        image.close()

        preview = self.client.get(f"/products/{product['id']}/drawing")
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.get_data().startswith(b"%PDF-1.4"))
        preview.close()

        drawing_edit = self.client.get(f"/products/{product['id']}/edit")
        drawing_edit_html = drawing_edit.get_data(as_text=True)
        self.assertIn("data-product-media-drawing-preview", drawing_edit_html)
        self.assertIn(f'formaction="/products/{product["id"]}/drawing/delete"', drawing_edit_html)
        self.assertIn(f'aria-label="删除 {product["bld_no"]} 的 PDF 图纸"', drawing_edit_html)
        self.assertNotIn("data-product-media-drawing-intake", drawing_edit_html)
        self.assertNotIn("预览当前图纸", drawing_edit_html)

        replace = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-001",
                "series": "TEST",
                "item": "DRAWING PART",
                "oe_no_1": "DRAW-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "active": "1",
                "drawing": (io.BytesIO(b"%PDF-1.4\nsecond drawing\n%%EOF"), "K-DRAW-001-v2.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(replace.status_code, 302)
        archive_dir = self.root / "data" / "drawings" / "archive" / "K-DRAW-001"
        self.assertTrue(list(archive_dir.glob("*.pdf")))

        batch = self.client.get("/products/drawings/batch")
        self.assertEqual(batch.status_code, 200)
        self.assertIn("暂未开放", batch.get_data(as_text=True))

    def test_stale_product_drawing_reference_uses_upload_state(self):
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-DRAW-STALE-001",
                    "series": "TEST",
                    "item": "STALE DRAWING PART",
                    "oe_no_1": "DRAW-STALE-001",
                    "models": "Tester",
                    "active": "1",
                },
                actor="tester",
            )
            product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-STALE-001",)).fetchone()
            self.assertIsNotNone(product)
            conn.execute(
                """
                UPDATE products
                SET drawing_path = ?, drawing_original_name = ?, drawing_updated_at = ?
                WHERE id = ?
                """,
                ("drawings/pdf/K-DRAW-STALE-001.pdf", "K-DRAW-STALE-001.pdf", "2026-07-26 10:00:00", product["id"]),
            )
            conn.commit()

        self.login()
        listing = self.client.get("/products", query_string={"bld": "K-DRAW-STALE-001"})
        listing_html = listing.get_data(as_text=True)
        self.assertEqual(listing.status_code, 200)
        self.assertIn("data-product-drawing-unavailable", listing_html)
        self.assertNotIn(f'href="/products/{product["id"]}/drawing"', listing_html)

        edit = self.client.get(f"/products/{product['id']}/edit")
        edit_html = edit.get_data(as_text=True)
        self.assertEqual(edit.status_code, 200)
        self.assertIn("data-product-media-drawing-intake", edit_html)
        self.assertNotIn("data-product-media-drawing-preview", edit_html)
        self.assertNotIn(f'formaction="/products/{product["id"]}/drawing/delete"', edit_html)

        preview = self.client.get(f"/products/{product['id']}/drawing", follow_redirects=False)
        self.assertEqual(preview.status_code, 302)
        self.assertIn("/products?bld=K-DRAW-STALE-001", preview.headers["Location"])

    def test_product_image_and_drawing_delete_endpoints(self):
        from app.modules.admin.persistence import save_user
        from app.modules.products.persistence import upsert_product

        with self.web.connect(self.web.DB_PATH) as conn:
            upsert_product(
                conn,
                {
                    "bld_no": "K-DELMEDIA-001",
                    "series": "TEST",
                    "item": "DELETE MEDIA PART",
                    "oe_no_1": "DELMEDIA-001",
                    "models": "Tester",
                    "active": "1",
                },
                actor="tester",
            )
            save_user(
                conn,
                {
                    "username": "viewer-delmedia",
                    "display_name": "Viewer Delete Media",
                    "password": "viewer-pw",
                    "role": "viewer",
                    "active": "1",
                },
                actor="tester",
            )
            conn.commit()
            product = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DELMEDIA-001",)).fetchone()
        product_id = product["id"]

        self.client.post("/logout")
        anonymous = self.client.post(f"/products/{product_id}/images/1/delete", data={})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login", anonymous.headers["Location"])

        self.login()
        upload = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DELMEDIA-001",
                "series": "TEST",
                "item": "DELETE MEDIA PART",
                "oe_no_1": "DELMEDIA-001",
                "oe_no_2": "",
                "models": "Tester",
                "price_cny": "",
                "active": "1",
                "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\ndelete image 1"), "K-DELMEDIA-001.png"),
                "product_image_2": (io.BytesIO(b"\x89PNG\r\n\x1a\ndelete image 2"), "K-DELMEDIA-001-2.png"),
                "drawing": (io.BytesIO(b"%PDF-1.4\ndelete drawing\n%%EOF"), "K-DELMEDIA-001.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(upload.status_code, 302)
        image_2_path = self.root / "data" / "product_images" / "K-DELMEDIA-001-2.png"
        drawing_path = self.root / "data" / "drawings" / "pdf" / "K-DELMEDIA-001.pdf"
        self.assertTrue(image_2_path.exists())
        self.assertTrue(drawing_path.exists())
        thumb_2_path = self.root / "data" / "product_images" / "thumbs" / "K-DELMEDIA-001-2.png"
        thumb_2_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_2_path.write_bytes(b"fake thumb")

        edit_html = self.client.get(f"/products/{product_id}/edit").get_data(as_text=True)
        self.assertIn(f'formaction="/products/{product_id}/images/1/delete"', edit_html)
        self.assertIn(f'formaction="/products/{product_id}/images/2/delete"', edit_html)
        self.assertIn(f'formaction="/products/{product_id}/drawing/delete"', edit_html)
        self.assertIn('data-confirm="确认删除图片 1？', edit_html)
        self.assertIn('data-confirm="确认删除 K-DELMEDIA-001 的 PDF 图纸？', edit_html)

        self.assertEqual(self.client.post(f"/products/{product_id}/images/0/delete").status_code, 400)
        self.assertEqual(self.client.post(f"/products/{product_id}/images/6/delete").status_code, 400)

        embedded_delete = self.client.post(
            f"/products/{product_id}/images/2/delete",
            data={"embedded": "1"},
            follow_redirects=False,
        )
        self.assertEqual(embedded_delete.status_code, 302)
        self.assertEqual(embedded_delete.headers["Location"], f"/products/{product_id}/edit?embedded=1")
        with self.web.connect(self.web.DB_PATH) as conn:
            updated = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        self.assertEqual(updated["image_path_2"], "")
        self.assertEqual(updated["image_path"], "data_product_images/K-DELMEDIA-001.png")
        self.assertFalse(image_2_path.exists())
        self.assertFalse(thumb_2_path.exists())
        image_archive = self.root / "data" / "product_images" / "archive" / "K-DELMEDIA-001"
        archived_images = list(image_archive.glob("*K-DELMEDIA-001-2.png"))
        self.assertEqual(len(archived_images), 1)
        self.assertEqual(archived_images[0].read_bytes(), b"\x89PNG\r\n\x1a\ndelete image 2")
        self.assertTrue((self.root / "data" / "product_images" / "K-DELMEDIA-001.png").exists())
        with self.web.connect(self.web.DB_PATH) as conn:
            image_audit = conn.execute(
                "SELECT * FROM audit_logs WHERE action = ? AND target_key = ? ORDER BY id DESC LIMIT 1",
                ("删除产品图片", "K-DELMEDIA-001"),
            ).fetchone()
        self.assertIsNotNone(image_audit)
        self.assertIn("图片 2", image_audit["detail"])

        drawing_delete = self.client.post(f"/products/{product_id}/drawing/delete", follow_redirects=False)
        self.assertEqual(drawing_delete.status_code, 302)
        self.assertEqual(drawing_delete.headers["Location"], f"/products/{product_id}/edit")
        with self.web.connect(self.web.DB_PATH) as conn:
            updated = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        self.assertEqual(updated["drawing_path"], "")
        self.assertEqual(updated["drawing_original_name"], "")
        self.assertEqual(updated["drawing_updated_at"], "")
        self.assertFalse(drawing_path.exists())
        drawing_archive = self.root / "data" / "drawings" / "archive" / "K-DELMEDIA-001"
        archived_drawings = list(drawing_archive.glob("*.pdf"))
        self.assertEqual(len(archived_drawings), 1)
        self.assertEqual(archived_drawings[0].read_bytes(), b"%PDF-1.4\ndelete drawing\n%%EOF")
        with self.web.connect(self.web.DB_PATH) as conn:
            drawing_audit = conn.execute(
                "SELECT * FROM audit_logs WHERE action = ? AND target_key = ? ORDER BY id DESC LIMIT 1",
                ("删除图纸", "K-DELMEDIA-001"),
            ).fetchone()
        self.assertIsNotNone(drawing_audit)

        edit_html = self.client.get(f"/products/{product_id}/edit").get_data(as_text=True)
        self.assertNotIn(f'formaction="/products/{product_id}/images/2/delete"', edit_html)
        self.assertNotIn(f'formaction="/products/{product_id}/drawing/delete"', edit_html)

        missing_image = self.client.post("/products/99999999/images/1/delete", follow_redirects=False)
        self.assertEqual(missing_image.status_code, 302)
        missing_drawing = self.client.post("/products/99999999/drawing/delete", follow_redirects=False)
        self.assertEqual(missing_drawing.status_code, 302)

        self.client.post("/logout")
        login = self.client.post(
            "/login",
            data={"username": "viewer-delmedia", "password": "viewer-pw", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)
        forbidden = self.client.post(
            f"/products/{product_id}/images/1/delete",
            headers={"Accept": "text/html", "X-Requested-With": "fetch"},
        )
        self.assertEqual(forbidden.status_code, 403)
        with self.web.connect(self.web.DB_PATH) as conn:
            unchanged = conn.execute("SELECT image_path FROM products WHERE id = ?", (product_id,)).fetchone()
        self.assertEqual(unchanged["image_path"], "data_product_images/K-DELMEDIA-001.png")
        self.client.post("/logout")
        self.login()

    def test_copy_product_media_restores_files_when_a_later_write_fails(self):
        self.login()
        source_upload = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-ROLLBACK-SOURCE",
                "series": "TEST",
                "item": "ROLLBACK PART",
                "oe_no_1": "DRAW-ROLLBACK",
                "models": "Tester",
                "active": "1",
                "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\nsource image"), "source.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(source_upload.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            source = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-ROLLBACK-SOURCE",)).fetchone()
        self.assertIsNotNone(source)

        with patch("app.modules.products.repository.save_product_drawing", side_effect=OSError("disk write failed")):
            copied = self.client.post(
                "/products/save",
                data={
                    "copy_source_product_id": str(source["id"]),
                    "bld_no": "K-DRAW-ROLLBACK-COPY",
                    "series": "TEST",
                    "item": "ROLLBACK PART",
                    "oe_no_1": "DRAW-ROLLBACK",
                    "models": "Tester",
                    "active": "1",
                    "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\noverride image"), "override.png"),
                    "drawing": (io.BytesIO(b"%PDF-1.4\nrollback drawing\n%%EOF"), "rollback.pdf"),
                },
                content_type="multipart/form-data",
                follow_redirects=False,
            )
        self.assertEqual(copied.status_code, 302)

        with self.web.connect(self.web.DB_PATH) as conn:
            target = conn.execute("SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-ROLLBACK-COPY",)).fetchone()
        self.assertIsNone(target)
        self.assertTrue((self.root / "data" / "product_images" / "K-DRAW-ROLLBACK-SOURCE.png").exists())
        self.assertFalse((self.root / "data" / "product_images" / "K-DRAW-ROLLBACK-COPY.png").exists())
        self.assertFalse((self.root / "data" / "product_images" / "thumbs" / "K-DRAW-ROLLBACK-COPY.png").exists())
        self.assertFalse((self.root / "data" / "product_images" / "archive" / "K-DRAW-ROLLBACK-COPY").exists())
        self.assertFalse((self.root / "data" / "drawings" / "pdf" / "K-DRAW-ROLLBACK-COPY.pdf").exists())
        self.assertFalse((self.root / "data" / "drawings" / "archive" / "K-DRAW-ROLLBACK-COPY").exists())
        self.assertFalse(list((self.root / "data" / "local-backups").glob("copy-product-media-*")))

    def test_copy_product_rejects_missing_source_media(self):
        self.login()
        headers = {"Accept": "application/json", "X-Requested-With": "fetch"}
        image_source_upload = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-MISSING-IMAGE-SOURCE",
                "series": "TEST",
                "item": "MISSING MEDIA PART",
                "oe_no_1": "DRAW-MISSING-IMAGE",
                "models": "Tester",
                "active": "1",
                "product_image_1": (io.BytesIO(b"\x89PNG\r\n\x1a\nsource image"), "source.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(image_source_upload.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            image_source = conn.execute(
                "SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-MISSING-IMAGE-SOURCE",)
            ).fetchone()
        self.assertIsNotNone(image_source)
        (self.root / "data" / "product_images" / "K-DRAW-MISSING-IMAGE-SOURCE.png").unlink()

        missing_image_copy = self.client.post(
            "/products/save",
            data={
                "copy_source_product_id": str(image_source["id"]),
                "bld_no": "K-DRAW-MISSING-IMAGE-COPY",
                "active": "1",
            },
            headers=headers,
            follow_redirects=False,
        )
        self.assertEqual(missing_image_copy.status_code, 400)
        self.assertIn("来源产品图片 1 文件未找到", missing_image_copy.get_json()["error"])

        drawing_source_upload = self.client.post(
            "/products/save",
            data={
                "bld_no": "K-DRAW-MISSING-PDF-SOURCE",
                "series": "TEST",
                "item": "MISSING MEDIA PART",
                "oe_no_1": "DRAW-MISSING-PDF",
                "models": "Tester",
                "active": "1",
                "drawing": (io.BytesIO(b"%PDF-1.4\nsource drawing\n%%EOF"), "source.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        self.assertEqual(drawing_source_upload.status_code, 302)
        with self.web.connect(self.web.DB_PATH) as conn:
            drawing_source = conn.execute(
                "SELECT * FROM products WHERE bld_no = ?", ("K-DRAW-MISSING-PDF-SOURCE",)
            ).fetchone()
        self.assertIsNotNone(drawing_source)
        (self.root / "data" / drawing_source["drawing_path"]).unlink()

        missing_drawing_copy = self.client.post(
            "/products/save",
            data={
                "copy_source_product_id": str(drawing_source["id"]),
                "bld_no": "K-DRAW-MISSING-PDF-COPY",
                "active": "1",
            },
            headers=headers,
            follow_redirects=False,
        )
        self.assertEqual(missing_drawing_copy.status_code, 400)
        self.assertIn("来源产品图纸文件未找到", missing_drawing_copy.get_json()["error"])

        with self.web.connect(self.web.DB_PATH) as conn:
            self.assertIsNone(
                conn.execute("SELECT 1 FROM products WHERE bld_no = ?", ("K-DRAW-MISSING-IMAGE-COPY",)).fetchone()
            )
            self.assertIsNone(
                conn.execute("SELECT 1 FROM products WHERE bld_no = ?", ("K-DRAW-MISSING-PDF-COPY",)).fetchone()
            )
