from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from app.excel_io import generate_excel_with_bld, preview_inquiry_columns, sanitize_inquiry_workbook_if_needed
from app.helpers import clean_original_filename, safe_upload_name, unique_prefixed_path
from app.matcher import ProductCatalog
from app.product_status import (
    format_product_status,
    product_status_header_for_price_mode,
    product_status_language_for_price_mode,
)

from .domain import (
    PRICE_LABELS,
    PriceOptions,
    attach_product_details,
    augment_summary_with_bld_fragments,
    bld_fragment_summary,
    export_price,
    looks_like_bld_shorthand,
)


ALLOWED_WORKBOOK_SUFFIXES = frozenset({".xls", ".xlsx"})


class WorkbookInquiryEngine:
    def __init__(
        self,
        *,
        base_dir: Path,
        upload_dir: Path,
        output_dir: Path,
    ) -> None:
        self.base_dir = base_dir.resolve()
        self.upload_dir = upload_dir.resolve()
        self.output_dir = output_dir.resolve()
        self.internal_upload_dir = self.upload_dir / "openclaw"
        self.internal_output_dir = self.output_dir / "openclaw"
        self.allowed_source_roots = (self.base_dir, self.upload_dir, self.output_dir)

    def internal_upload_path(self, filename: str, *, prefix: str) -> Path:
        self.internal_upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_upload_name(filename)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return unique_prefixed_path(self.internal_upload_dir, f"{prefix}-{timestamp}-{safe_name}")

    def resolve_source_path(self, raw_path: object) -> Path:
        if not raw_path:
            raise ValueError("请传 file_path，或以 multipart/form-data 上传 file。")
        source = Path(str(raw_path)).expanduser()
        source = (self.base_dir / source).resolve() if not source.is_absolute() else source.resolve()
        if not source.exists() or not source.is_file():
            raise ValueError(f"文件不存在：{source}")
        if source.suffix.lower() not in ALLOWED_WORKBOOK_SUFFIXES:
            raise ValueError("客户原始文件仅支持 .xls 或 .xlsx。")
        if not any(root == source or root in source.parents for root in self.allowed_source_roots):
            allowed = "、".join(str(root) for root in self.allowed_source_roots)
            raise ValueError(f"file_path 不在允许读取范围内。允许范围：{allowed}")
        return source

    def new_number_output_path(self, source_name: object) -> Path:
        return self._new_output_path(source_name, suffix=".xlsx", fallback="客户询价")

    def new_source_output_path(self, source_name: object, *, suffix: str, fallback: str) -> Path:
        return self._new_output_path(source_name, suffix=suffix, fallback=fallback)

    def _new_output_path(self, source_name: object, *, suffix: str, fallback: str) -> Path:
        name = clean_original_filename(str(source_name or ""), fallback_suffix="")
        stem = Path(name).stem.strip() or fallback
        filename = f"re{datetime.now().strftime('%y%m%d')}_{stem}{suffix}"
        self.internal_output_dir.mkdir(parents=True, exist_ok=True)
        return unique_prefixed_path(self.internal_output_dir, filename)

    def write_numbers_workbook(self, numbers: list[str], path: Path) -> Path:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "OpenClaw号码"
        sheet.append(["OE号"])
        for number in numbers:
            sheet.append([number])
        sheet.column_dimensions["A"].width = 28
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(path)
        workbook.close()
        return path

    def write_numbers_summary(self, summary: dict, output_path: Path, options: PriceOptions) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "OpenClaw号码"
        headers = ["OE号", "BLD NO."]
        price_label = PRICE_LABELS.get(options.price_mode, "")
        if price_label:
            headers.append(price_label)
            headers.append(product_status_header_for_price_mode(options.price_mode))
        headers.extend(["匹配说明", "产品名称", "车型"])
        sheet.append(headers)
        for row in summary.get("rows", []):
            values = [row.get("oe") or row.get("name") or "", row.get("bld_no") or ""]
            if price_label:
                values.append(export_price(row.get("price_cny"), options))
                values.append(
                    format_product_status(
                        row.get("product_status"),
                        product_status_language_for_price_mode(options.price_mode),
                    )
                )
            product = row.get("product") or {}
            values.extend(
                [
                    row.get("match_note") or row.get("reason") or "",
                    product.get("item", "") if isinstance(product, dict) else "",
                    product.get("models", "") if isinstance(product, dict) else "",
                ]
            )
            sheet.append(values)
        for letter, width in {"A": 28, "B": 18, "C": 14, "D": 18, "E": 34, "F": 26, "G": 34}.items():
            sheet.column_dimensions[letter].width = width
        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)
        workbook.close()

    def analyze_numbers(
        self,
        numbers: list[str],
        catalog: ProductCatalog,
        options: PriceOptions,
        *,
        persistent_source_path: Path | None = None,
        output_path: Path | None = None,
    ) -> tuple[dict, Path | None]:
        source_path = persistent_source_path
        if len(numbers) == 1 and looks_like_bld_shorthand(numbers[0]):
            summary = bld_fragment_summary(catalog, numbers[0])
        elif source_path is not None:
            self.write_numbers_workbook(numbers, source_path)
            summary = generate_excel_with_bld(
                source_path,
                self.internal_output_dir / "__analysis.xlsx",
                catalog,
                write_output=False,
                **options.as_kwargs(),
            )
            summary = augment_summary_with_bld_fragments(summary, catalog)
        else:
            with tempfile.TemporaryDirectory(prefix="bld-inquiry-analysis-") as temporary_dir:
                temporary_source = self.write_numbers_workbook(
                    numbers,
                    Path(temporary_dir) / "numbers.xlsx",
                )
                summary = generate_excel_with_bld(
                    temporary_source,
                    self.internal_output_dir / "__analysis.xlsx",
                    catalog,
                    write_output=False,
                    **options.as_kwargs(),
                )
            summary = augment_summary_with_bld_fragments(summary, catalog)
        summary = attach_product_details(summary, catalog)
        if output_path is not None:
            self.write_numbers_summary(summary, output_path, options)
        return summary, source_path

    def analyze_workbook(
        self,
        source_path: Path,
        output_path: Path,
        catalog: ProductCatalog,
        *,
        match_column: object = None,
        write_output: bool,
        options: PriceOptions,
    ) -> dict:
        return generate_excel_with_bld(
            source_path,
            output_path,
            catalog,
            match_column=match_column,
            write_output=write_output,
            **options.as_kwargs(),
        )

    def sanitize(self, source_path: Path) -> object:
        return sanitize_inquiry_workbook_if_needed(
            source_path,
            self.internal_upload_path(source_path.name, prefix="source-cleaned"),
        )

    def preview(self, path: Path, *, max_rows: int = 8, max_cols: int = 20) -> dict:
        return preview_inquiry_columns(path, max_rows=max_rows, max_cols=max_cols)
