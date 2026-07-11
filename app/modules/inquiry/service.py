from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.platform.artifacts import ArtifactRecord, SQLiteArtifactStore

from .domain import (
    CatalogUnavailableError,
    InquiryLimits,
    InquiryValidationError,
    PriceOptions,
    extract_numbers,
    format_rows,
    parse_limits,
    parse_match_columns,
    parse_price_options,
    pasted_inquiry_codes,
    payload_value,
    quick_search,
    should_render_pasted_result,
)
from .infrastructure import WorkbookInquiryEngine
from .ports import CatalogProvider, InquiryUnitOfWorkFactory


class InquiryWorkbookError(RuntimeError):
    def __init__(self, message: str, *, column_preview: dict | None = None) -> None:
        super().__init__(message)
        self.column_preview = column_preview


@dataclass(frozen=True, slots=True)
class InquiryExecution:
    mode: str
    summary: dict
    options: PriceOptions
    limits: InquiryLimits
    invalid_items: list[str]
    output_path: Path | None = None
    source_path: Path | None = None
    processed_source_path: Path | None = None
    cleanup_message: str = ""
    artifact: ArtifactRecord | None = None

    def _rows(self) -> tuple[list[dict], list[str], bool]:
        return format_rows(self.summary, self.options, self.limits)

    def legacy_payload(self) -> dict[str, object]:
        rows, unmatched_list, rows_truncated = self._rows()
        summary_payload = {
            "total_rows": self.summary.get("total", 0),
            "matched_count": self.summary.get("matched", 0),
            "unmatched_count": self.summary.get("unmatched", 0),
            "returned_rows": len(rows),
            "rows_truncated": rows_truncated,
            "invalid_items": self.invalid_items,
            "price_mode": self.options.price_mode,
            "export_price_label": {
                "none": "",
                "tax": "含税单价",
                "net": "不含税单价",
                "usd": "美金价",
            }.get(self.options.price_mode, ""),
            "output_generated": self.output_path is not None,
        }
        return {
            "ok": True,
            "mode": self.mode,
            "summary": summary_payload,
            "matched_count": summary_payload["matched_count"],
            "unmatched_count": summary_payload["unmatched_count"],
            "rows": rows,
            "unmatched_list": unmatched_list,
            "invalid_items": self.invalid_items,
            "source_path": str(self.source_path.resolve()) if self.source_path else None,
            "processed_source_path": (
                str(self.processed_source_path.resolve()) if self.processed_source_path else None
            ),
            "cleanup_message": self.cleanup_message,
            "output_path": str(self.output_path.resolve()) if self.output_path else None,
            "output_name": self.output_path.name if self.output_path else None,
        }

    def api_payload(self) -> dict[str, object]:
        rows, unmatched_list, rows_truncated = self._rows()
        return {
            "mode": self.mode,
            "summary": {
                "total_rows": int(self.summary.get("total", 0)),
                "matched_count": int(self.summary.get("matched", 0)),
                "unmatched_count": int(self.summary.get("unmatched", 0)),
                "returned_rows": len(rows),
                "rows_truncated": rows_truncated,
                "invalid_items": self.invalid_items,
                "price_mode": self.options.price_mode,
            },
            "rows": rows,
            "unmatched_list": unmatched_list,
            "artifact": self.artifact.api_payload() if self.artifact else None,
        }


class InquiryService:
    def __init__(
        self,
        catalog_port: CatalogProvider,
        engine: WorkbookInquiryEngine,
        unit_of_work_factory: InquiryUnitOfWorkFactory,
        artifact_store: SQLiteArtifactStore,
    ) -> None:
        self.catalog_port = catalog_port
        self.engine = engine
        self.unit_of_work_factory = unit_of_work_factory
        self.artifact_store = artifact_store

    def _catalog(self):
        catalog = self.catalog_port.catalog()
        if catalog is None:
            raise CatalogUnavailableError("请先导入产品目录。")
        return catalog

    def run_numbers(
        self,
        payload: Mapping[str, object],
        *,
        export: bool,
        actor: str,
        artifact_owner: str | None = None,
    ) -> InquiryExecution:
        catalog = self._catalog()
        options = parse_price_options(payload)
        limits = parse_limits(payload)
        numbers, invalid_items = extract_numbers(payload)
        if not numbers:
            raise InquiryValidationError(
                "inquiry.numbers_required",
                "请传 numbers 数组，或 text 文本号码。",
                {"invalid_items": invalid_items},
            )
        source_name = payload_value(
            payload,
            "source_name",
            "source_filename",
            "original_filename",
            "output_name",
        )
        if export and not source_name:
            raise InquiryValidationError(
                "inquiry.source_name_required",
                "号码数组或文字号码生成 Excel 时必须传 source_name，作为文件名中间的“源文件名称”。",
            )
        output_path = self.engine.new_number_output_path(source_name) if export else None
        source_path = (
            self.engine.internal_upload_path("numbers.xlsx", prefix="numbers") if export else None
        )
        summary, source_path = self.engine.analyze_numbers(
            numbers,
            catalog,
            options,
            persistent_source_path=source_path,
            output_path=output_path,
        )
        artifact = None
        if output_path is not None:
            detail = (
                f"OpenClaw 号码查询 {summary['total']} 行，命中 {summary['matched']} 行，"
                f"未找到 {summary['unmatched']} 行"
            )
            self._audit(
                "内部 API 生成号码结果",
                "internal_api",
                output_path.name,
                detail,
                actor=actor,
            )
            if artifact_owner:
                artifact = self.artifact_store.register(output_path, owner_id=artifact_owner)
        return InquiryExecution(
            mode="new-workbook",
            summary=summary,
            options=options,
            limits=limits,
            invalid_items=invalid_items,
            output_path=output_path,
            source_path=source_path,
            artifact=artifact,
        )

    def run_file(
        self,
        source_path: Path,
        original_filename: str,
        payload: Mapping[str, object],
        *,
        export: bool,
        actor: str,
        artifact_owner: str | None = None,
    ) -> InquiryExecution:
        catalog = self._catalog()
        options = parse_price_options(payload)
        limits = parse_limits(payload)
        match_column = parse_match_columns(payload)
        output_path = None
        if export:
            source_name = payload_value(
                payload,
                "source_name",
                "source_filename",
                default=original_filename,
            )
            output_path = self.engine.new_source_output_path(
                source_name,
                suffix=source_path.suffix.lower(),
                fallback="客户询价",
            )
        cleanup = self.engine.sanitize(source_path)
        processing_source = cleanup.path
        try:
            summary = self.engine.analyze_workbook(
                processing_source,
                output_path or (self.engine.internal_output_dir / "__analysis.xlsx"),
                catalog,
                match_column=match_column,
                write_output=export,
                options=options,
            )
        except Exception as exc:
            try:
                preview = self.engine.preview(processing_source, max_rows=5, max_cols=8)
            except Exception:
                preview = None
            raise InquiryWorkbookError(
                "分析客户原始文件失败，请检查文件结构和匹配列。",
                column_preview=preview,
            ) from exc
        artifact = None
        if output_path is not None:
            detail = (
                f"OpenClaw 增强客户原始文件 {summary['total']} 行，命中 {summary['matched']} 行，"
                f"未找到 {summary['unmatched']} 行"
            )
            if cleanup.cleaned:
                detail = f"{detail}；{cleanup.message}"
            self._audit(
                "内部 API 生成增强询价文件",
                "internal_api",
                output_path.name,
                detail,
                actor=actor,
            )
            if artifact_owner:
                artifact = self.artifact_store.register(output_path, owner_id=artifact_owner)
        return InquiryExecution(
            mode="augment-source-workbook",
            summary=summary,
            options=options,
            limits=limits,
            invalid_items=[],
            output_path=output_path,
            source_path=source_path,
            processed_source_path=processing_source if cleanup.cleaned else None,
            cleanup_message=cleanup.message if cleanup.cleaned else "",
            artifact=artifact,
        )

    def quick_search(self, query: str) -> list[dict]:
        return quick_search(self.catalog_port.catalog(), query)

    def catalog_available(self) -> bool:
        return self.catalog_port.catalog() is not None

    def pasted_codes(self, query: str) -> list[str]:
        return pasted_inquiry_codes(query, self._catalog())

    def should_render_pasted(self, query: str, codes: list[str]) -> bool:
        return should_render_pasted_result(query, codes)

    def analyze_pasted(
        self,
        codes: list[str],
        *,
        upload_path: Path,
        actor: str,
    ) -> dict:
        summary, _ = self.engine.analyze_numbers(
            codes,
            self._catalog(),
            PriceOptions("none"),
            persistent_source_path=upload_path,
        )
        for index, row in enumerate(summary.get("rows", []), start=1):
            row["row"] = index
        self._audit(
            "预览粘贴号码匹配结果",
            "inquiry",
            "粘贴号码询价.xlsx",
            (
                f"粘贴 {len(codes)} 个号码；共 {summary['total']} 行，命中 {summary['matched']} 行，"
                f"未找到 {summary['unmatched']} 行"
            ),
            actor=actor,
        )
        return summary

    def analyze_workbook(
        self,
        source_path: Path,
        output_path: Path,
        *,
        match_column: object = None,
        write_output: bool = False,
        options: PriceOptions | None = None,
    ) -> dict:
        return self.engine.analyze_workbook(
            source_path,
            output_path,
            self._catalog(),
            match_column=match_column,
            write_output=write_output,
            options=options or PriceOptions("none"),
        )

    def preview_columns(self, source_path: Path) -> dict:
        return self.engine.preview(source_path)

    def record_cleanup(self, filename: str, message: str, *, actor: str) -> None:
        self._audit("自动清理询价文件", "inquiry", filename, message, actor=actor)

    def record_export(
        self,
        filename: str,
        summary: dict,
        detail_prefix: str,
        *,
        detail_suffix: str = "",
        actor: str,
    ) -> None:
        detail = (
            f"{detail_prefix}共 {summary['total']} 行，命中 {summary['matched']} 行，"
            f"未找到 {summary['unmatched']} 行{detail_suffix}"
        )
        self._audit("生成匹配结果", "inquiry", filename, detail, actor=actor)

    def package_drawings(
        self,
        summary_rows: list[dict],
        output_path: Path,
        *,
        detail_prefix: str,
        matched: int,
        actor: str,
    ) -> dict:
        with self.unit_of_work_factory() as unit_of_work:
            package = unit_of_work.repository.build_drawings(summary_rows, output_path)
            detail = (
                f"{detail_prefix}共 {matched} 行命中，打包 PDF {package['added']} 个，"
                f"缺少 {package['missing']} 个"
            )
            unit_of_work.repository.audit(
                "生成图纸压缩包",
                "drawing_zip",
                output_path.name,
                detail,
                actor=actor,
            )
            unit_of_work.commit()
        return package

    def save_alias(
        self,
        source_code: str,
        bld_no: str,
        note: str,
        sync_target: str,
        *,
        actor: str,
    ) -> bool:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.save_alias(source_code, bld_no, note, actor=actor)
            appended = False
            if sync_target in {"oe", "brand_code"}:
                appended = unit_of_work.repository.append_product_code(
                    bld_no,
                    source_code,
                    sync_target,
                    actor=actor,
                )
            unit_of_work.commit()
        self.catalog_port.invalidate_catalog()
        return appended

    def delete_alias(self, source_code: str, *, actor: str) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.delete_alias(source_code, actor=actor)
            unit_of_work.commit()
        self.catalog_port.invalidate_catalog()

    def _audit(
        self,
        action: str,
        target_type: str,
        target_key: str,
        detail: str,
        *,
        actor: str,
    ) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.audit(
                action,
                target_type,
                target_key,
                detail,
                actor=actor,
            )
            unit_of_work.commit()
