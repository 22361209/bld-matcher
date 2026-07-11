from __future__ import annotations

import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from app.helpers import clean_original_filename, safe_upload_name
from app.locks import ImportLockError, import_lock
from app.material_sheet import (
    create_plan_template,
    generate_material_sheet_from_materials,
    material_data_stats,
    sync_material_specs_from_dimensions,
)


ALLOWED_DRAWING_SUFFIXES = frozenset({".pdf"})
DEFAULT_DRAWING_CATEGORY = "球销"


class MaterialImportBusyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaterialSourceReceipt:
    target: Path
    backup: Path | None
    existed: bool
    normalized: int
    stats: dict[str, object]


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=16)
def _cached_stats(path_text: str, signature: tuple[int, int]) -> dict[str, object]:
    return material_data_stats(Path(path_text))


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


class MaterialFileAdapter:
    def __init__(
        self,
        *,
        data_dir: Path,
        material_data_path: Path,
        template_path: Path,
        drawing_dir: Path,
        lock_factory=import_lock,
    ) -> None:
        self.data_dir = data_dir
        self.material_data_path = material_data_path
        self.template_path = template_path
        self.drawing_dir = drawing_dir
        self.lock_factory = lock_factory

    def source_stats(self) -> dict[str, object]:
        return _cached_stats(str(self.material_data_path), _file_signature(self.material_data_path))

    def source_path(self) -> Path | None:
        return self.material_data_path if self.material_data_path.exists() else None

    def create_template(self) -> Path:
        self.template_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.template_path.with_name(f".{self.template_path.name}.{uuid4().hex}.tmp.xlsx")
        try:
            create_plan_template(temporary)
            os.replace(temporary, self.template_path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.template_path

    def generate_sheet(
        self,
        material_rows: dict[str, list[dict]],
        plan_path: Path,
        output_dir: Path,
        *,
        filename_prefix: str,
    ) -> tuple[Path, dict]:
        return generate_material_sheet_from_materials(
            material_rows,
            plan_path,
            output_dir,
            filename_prefix=filename_prefix,
        )

    @contextmanager
    def import_guard(self, actor: str):
        try:
            with self.lock_factory(actor, "材料数据导入"):
                yield
        except ImportLockError as exc:
            raise MaterialImportBusyError(str(exc)) from exc

    def install_source(self, upload_path: Path) -> MaterialSourceReceipt:
        stats = material_data_stats(upload_path)
        if stats.get("invalid"):
            raise ValueError(str(stats.get("error") or "文件里必须包含“材料数据”工作表。"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        existed = self.material_data_path.exists()
        backup = None
        if existed:
            backup = self.data_dir / f"stamping_materials-backup-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.xlsx"
            shutil.copy2(self.material_data_path, backup)
        temporary = self.material_data_path.with_name(
            f".{self.material_data_path.stem}.{uuid4().hex}.incoming{self.material_data_path.suffix}"
        )
        try:
            shutil.copy2(upload_path, temporary)
            normalized = sync_material_specs_from_dimensions(temporary)
            os.replace(temporary, self.material_data_path)
        finally:
            temporary.unlink(missing_ok=True)
        _cached_stats.cache_clear()
        return MaterialSourceReceipt(
            target=self.material_data_path,
            backup=backup,
            existed=existed,
            normalized=normalized,
            stats=stats,
        )

    def rollback_source(self, receipt: MaterialSourceReceipt) -> None:
        if receipt.existed and receipt.backup and receipt.backup.is_file():
            temporary = receipt.target.with_name(f".{receipt.target.name}.{uuid4().hex}.rollback")
            try:
                shutil.copy2(receipt.backup, temporary)
                os.replace(temporary, receipt.target)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            receipt.target.unlink(missing_ok=True)
        _cached_stats.cache_clear()

    def drawing_records(self) -> list[dict[str, object]]:
        self.drawing_dir.mkdir(parents=True, exist_ok=True)
        return [
            self._drawing_record(path)
            for path in sorted(self.drawing_dir.glob("*.pdf"), key=lambda item: _natural_key(item.stem))
        ]

    def filter_drawings(self, *, query: str, category: str) -> tuple[list[dict[str, object]], list[str], int]:
        records = self.drawing_records()
        categories = sorted({str(record["category"]) for record in records}, key=_natural_key)
        selected_category = category if category in categories else ""
        needle = query.strip().lower()
        filtered = []
        for record in records:
            values = (str(record["code"]), str(record["category"]), str(record["name"]))
            if selected_category and selected_category != record["category"]:
                continue
            if needle and not any(needle in value.lower() for value in values):
                continue
            filtered.append(record)
        return filtered, categories, len(records)

    def save_drawing(self, file) -> Path:
        filename = str(getattr(file, "filename", "") or "")
        if Path(filename).suffix.lower() not in ALLOWED_DRAWING_SUFFIXES:
            raise ValueError("物料图纸目前仅支持 PDF 文件。")
        self.drawing_dir.mkdir(parents=True, exist_ok=True)
        destination = self._unique_drawing_path(filename)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.upload")
        try:
            file.save(temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def resolve_drawing(self, name: str) -> Path | None:
        filename = Path(name or "").name
        path = (self.drawing_dir / filename).resolve()
        if path.parent != self.drawing_dir.resolve():
            return None
        if not path.is_file() or path.suffix.lower() not in ALLOWED_DRAWING_SUFFIXES:
            return None
        return path

    def _unique_drawing_path(self, filename: str) -> Path:
        safe_name = safe_upload_name(clean_original_filename(filename, fallback_suffix=".pdf"))
        candidate = self.drawing_dir / safe_name
        if not candidate.exists():
            return candidate
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return self.drawing_dir / f"{candidate.stem}-{timestamp}{candidate.suffix}"

    @staticmethod
    def _drawing_record(path: Path) -> dict[str, object]:
        stat = path.stat()
        return {
            "code": path.stem.strip(),
            "category": DEFAULT_DRAWING_CATEGORY,
            "name": path.name,
            "size_kb": max(1, round(stat.st_size / 1024)),
            "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
