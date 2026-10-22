from __future__ import annotations

import logging

from flask import jsonify, request

from app.security import actor_name, permission_required

from .factory import get_product_service
from .service import ProductCatalogVisibilityConflictError, ProductNotFoundError

logger = logging.getLogger(__name__)


def register(app) -> None:
    @app.post("/products/<int:product_id>/catalog-visibility")
    @permission_required("view_products")
    @permission_required("edit_products")
    def update_product_catalog_visibility(product_id: int):
        try:
            visible = request.form.get("visible")
            expected_visible = request.form.get("expected_visible")
            if visible not in {"0", "1"} or expected_visible not in {"0", "1"}:
                raise ValueError("目录可见设置必须为 0 或 1。")
            product = get_product_service().update_catalog_visibility(
                product_id, language=request.form.get("language", ""), visible=visible == "1",
                expected_visible=expected_visible == "1", expected_updated_at=request.form.get("expected_updated_at", ""),
                actor=actor_name(),
            )
        except ProductNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except ProductCatalogVisibilityConflictError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception:
            logger.exception("Catalog visibility update failed", extra={"error_code": "product.catalog_visibility_failed"})
            return jsonify({"ok": False, "error": "保存失败，请刷新目录确认后重试。"}), 500
        return jsonify({"ok": True, "product_id": product.id, "updated_at": product.updated_at,
                        "catalog_visible_en": product.catalog_visible_en, "catalog_visible_zh": product.catalog_visible_zh})
