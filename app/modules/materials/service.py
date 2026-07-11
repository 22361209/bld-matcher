from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from .domain import MaterialImportResult, MaterialPage, normalize_status
from .infrastructure import MaterialFileAdapter


class MaterialService:
    def __init__(
        self,
        unit_of_work_factory,
        bootstrap: Callable[[], None],
        files: MaterialFileAdapter,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.bootstrap = bootstrap
        self.files = files

    def stats(self) -> dict[str, int]:
        self.bootstrap()
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.stats()

    def list_items(self, *, query: str, status: str, limit: int, offset: int) -> MaterialPage:
        self.bootstrap()
        normalized_status = normalize_status(status)
        safe_limit = max(1, min(500, int(limit)))
        safe_offset = max(0, int(offset))
        with self.unit_of_work_factory() as unit_of_work:
            total = unit_of_work.repository.count(query=query, status=normalized_status)
            records = unit_of_work.repository.list(
                query=query,
                status=normalized_status,
                limit=safe_limit,
                offset=safe_offset,
            )
            stats = unit_of_work.repository.stats()
        return MaterialPage(records=records, total=total, limit=safe_limit, offset=safe_offset, stats=stats)

    def get_item(self, item_id: int) -> dict[str, object] | None:
        self.bootstrap()
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.get(item_id)

    def save_item(self, data: Mapping[str, object], *, actor: str) -> int:
        with self.unit_of_work_factory() as unit_of_work:
            item_id = unit_of_work.repository.save(data, actor=actor)
            unit_of_work.commit()
        return item_id

    def deactivate_item(self, item_id: int, *, actor: str) -> None:
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.repository.deactivate(item_id, actor=actor)
            unit_of_work.commit()

    def create_template(self) -> Path:
        return self.files.create_template()

    def generate_sheet(
        self,
        plan_path: Path,
        output_dir: Path,
        *,
        filename_prefix: str,
        actor: str,
    ) -> tuple[Path, dict]:
        self.bootstrap()
        output_path: Path | None = None
        try:
            with self.unit_of_work_factory() as unit_of_work:
                rows = unit_of_work.repository.sheet_rows()
                if not rows:
                    raise ValueError("还没有可用的材料明细，请先上传或新增材料数据。")
                output_path, summary = self.files.generate_sheet(
                    rows,
                    plan_path,
                    output_dir,
                    filename_prefix=filename_prefix,
                )
                missing_text = f"，未匹配 {len(summary['missing'])} 个型号" if summary["missing"] else ""
                unit_of_work.repository.audit(
                    "生成生产料单",
                    "material_sheet",
                    output_path.name,
                    f"生产计划 {summary['plan_count']} 行，料单明细 {summary['detail_count']} 行，规格 {summary['spec_count']} 个{missing_text}",
                    actor=actor,
                )
                unit_of_work.commit()
            return output_path, summary
        except Exception:
            if output_path is not None:
                output_path.unlink(missing_ok=True)
            raise

    def import_data(self, upload_path: Path, *, original_name: str, actor: str) -> MaterialImportResult:
        with self.files.import_guard(actor):
            receipt = self.files.install_source(upload_path)
            try:
                with self.unit_of_work_factory() as unit_of_work:
                    imported = unit_of_work.repository.import_data(receipt.target, actor=actor)
                    unit_of_work.repository.audit(
                        "更新材料数据文件",
                        "material_data",
                        original_name,
                        f"型号 {receipt.stats['model_count']} 个，明细 {receipt.stats['detail_count']} 行；规格尺寸重算 {receipt.normalized} 行；导入数据库 {imported} 行",
                        actor=actor,
                    )
                    unit_of_work.commit()
            except Exception:
                self.files.rollback_source(receipt)
                raise
        return MaterialImportResult(imported=imported, normalized=receipt.normalized, stats=receipt.stats)

    def history_rows(self, paths: list[Path], query: str) -> list[dict[str, object]]:
        needle = query.strip().lower()
        rows = []
        for path in paths:
            parent = path.parent.name
            operator = parent.split("-", 1)[1] if parent.startswith("u") and "-" in parent else "历史文件"
            stat = path.stat()
            updated_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            haystack = " ".join([path.name, path.suffix.lower().lstrip(".").upper(), operator, updated_at]).lower()
            if needle and needle not in haystack:
                continue
            rows.append(
                {
                    "path": path,
                    "name": path.name,
                    "kind": path.suffix.lower().lstrip(".").upper(),
                    "operator": operator,
                    "updated_at": updated_at,
                }
            )
        return rows

    def drawing_page(self, *, query: str, category: str, selected_name: str) -> dict[str, object]:
        drawings, categories, total = self.files.filter_drawings(query=query, category=category)
        normalized_category = category if category in categories else ""
        selected = next((drawing for drawing in drawings if drawing["name"] == Path(selected_name).name), None)
        if selected is None and drawings:
            selected = drawings[0]
        return {
            "drawings": drawings,
            "selected_drawing": selected,
            "total_drawings": total,
            "categories": categories,
            "category": normalized_category,
            "query": query,
        }

    def upload_drawing(self, file, *, actor: str) -> Path:
        destination = self.files.save_drawing(file)
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.repository.audit(
                    "上传物料图纸",
                    "material_drawing",
                    destination.name,
                    f"上传物料图纸 {destination.name}",
                    actor=actor,
                )
                unit_of_work.commit()
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def drawing_path(self, name: str) -> Path | None:
        return self.files.resolve_drawing(name)

    def source_stats(self) -> dict[str, object]:
        return self.files.source_stats()

    def source_path(self) -> Path | None:
        return self.files.source_path()
