from __future__ import annotations

from pathlib import Path

from app.product_media import resolve_product_image_path, resolve_product_image_thumb_path

from .purchase_pdf import generate_purchase_contract_pdf
from .sales_pdf import generate_sales_contract_pdf


class ContractPdfAdapter:
    def generate(self, kind: str, contract: dict, output_path: Path) -> None:
        if kind == "sales":
            generate_sales_contract_pdf(contract, output_path)
            return
        generate_purchase_contract_pdf(contract, output_path)


class ContractProductImageResolver:
    def __init__(
        self,
        *,
        base_dir: Path,
        product_image_dir: Path,
        data_prefix: str,
    ) -> None:
        self.base_dir = base_dir
        self.product_image_dir = product_image_dir
        self.data_prefix = data_prefix

    @staticmethod
    def _existing(path: Path | None) -> Path | None:
        return path if path and path.exists() and path.is_file() else None

    def __call__(self, product: dict[str, object]) -> Path | None:
        explicit = str(product.get("image_path") or "")
        if explicit.startswith(self.data_prefix):
            name = explicit[len(self.data_prefix) :]
            return self._existing(resolve_product_image_thumb_path(name)) or self._existing(resolve_product_image_path(name))
        if explicit.startswith("/static/"):
            return self._existing(self.base_dir / explicit.lstrip("/"))
        if explicit:
            return self._existing(self.base_dir / "static" / explicit.lstrip("/")) or self._existing(
                self.product_image_dir / Path(explicit).name
            )

        bld_no = str(product.get("bld_no") or "")
        for suffix in ("jpg", "jpeg", "png", "webp"):
            candidates = (
                self.product_image_dir / f"{bld_no}.{suffix}",
                self.base_dir / "static" / "product_images" / "thumbs" / f"{bld_no}.{suffix}",
                self.base_dir / "static" / "product_images" / f"{bld_no}.{suffix}",
            )
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    return candidate
        return None
