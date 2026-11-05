from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .domain import (
    DEFAULT_MATERIAL_ATTRIBUTE, CostingConflict, CostingNotFound, CostingValidationError,
    calculate_cost, reference_snapshot, text, with_pricing,
)
from .grid import column_filter_options, matches_columns, normalize_column_filters
from .line_code_maintenance import plan_line_codes
from .ports import CostingUnitOfWorkFactory
from .repository import SQLiteCostingUnitOfWork


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class CostingService:
    def __init__(self, db_path: Path, *, unit_of_work_factory: CostingUnitOfWorkFactory | None = None) -> None:
        self.db_path = Path(db_path)
        self._uow = unit_of_work_factory or (lambda write: SQLiteCostingUnitOfWork(self.db_path, write=write))

    def calculate(self, data: Mapping[str, object], *, previous: dict | None = None) -> dict:
        return calculate_cost(data, previous=previous)

    def list(self, q: str = "", kind: str = "", status: str = "", page: int = 1,
             page_size: int = 50, sort: str = "code", column_filters: Mapping[str, object] | None = None) -> dict:
        q = self._list_query(q, kind, status)
        selected = normalize_column_filters(column_filters)
        if sort not in {"updated", "code", "code_desc"}:
            raise CostingValidationError("排序条件无效。", "sort")
        try:
            page, page_size = max(1, int(page)), max(1, min(200, int(page_size)))
        except (ValueError, TypeError) as exc:
            raise CostingValidationError("分页参数无效。", "page") from exc
        with self._uow(False) as uow:
            matching = None
            if selected:
                summaries = (with_pricing(row) for row in uow.repository.summaries(q=q, kind=kind, status=status, sort=sort))
                matching = [row["id"] for row in summaries if matches_columns(row, selected)]
            total = len(matching) if matching is not None else uow.repository.count(q=q, kind=kind, status=status)
            pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, pages)
            offset = (page - 1) * page_size
            records = uow.repository.get_many(matching[offset:offset + page_size]) if matching is not None else (
                uow.repository.list(q=q, kind=kind, status=status, sort=sort, limit=page_size, offset=offset)
            )
        records = [with_pricing(record) for record in records]
        return {"records": records, "total": total, "page": page, "page_size": page_size, "pages": pages}

    @staticmethod
    def _list_query(q: str, kind: str, status: str) -> str:
        if kind not in {"", "arm", "ball"} or status not in {"", "ready", "draft"}:
            raise CostingValidationError("筛选条件无效。")
        return text(q, "q")

    def filter_options(self, q: str = "", kind: str = "", status: str = "",
                       column_filters: Mapping[str, object] | None = None) -> dict[str, list[dict[str, object]]]:
        q = self._list_query(q, kind, status)
        selected = normalize_column_filters(column_filters)
        with self._uow(False) as uow:
            records = [with_pricing(row) for row in uow.repository.summaries(q=q, kind=kind, status=status, sort="code")]
        return column_filter_options(records, selected)

    def get(self, record_id: str) -> dict:
        with self._uow(False) as uow:
            record = uow.repository.get(record_id)
        if record is None:
            raise CostingNotFound()
        return with_pricing(record)

    @staticmethod
    def _actor(actor: str) -> str:
        actor = text(actor, "actor")
        if not actor:
            raise CostingValidationError("写入成本必须记录操作人。", "actor")
        return actor

    @staticmethod
    def _new_record(data: Mapping[str, object], actor: str, source: str) -> dict:
        record = calculate_cost(data)
        now = _timestamp()
        record.update(id=uuid4().hex, version=1, created_at=now, updated_at=now,
                      created_by=actor, updated_by=actor, source=source)
        for item in record["items"]:
            item["id"] = uuid4().hex
            if source == "manual":
                item.setdefault("material_attribute", DEFAULT_MATERIAL_ATTRIBUTE)
        return record

    def create(self, data: Mapping[str, object], *, actor: str, source: str = "manual") -> dict:
        actor = self._actor(actor)
        if source not in {"manual", "excel", "api", "copy"}:
            raise CostingValidationError("成本来源无效。", "source")
        record = self._new_record(data, actor, source)
        with self._uow(True) as uow:
            uow.repository.insert(record)
            uow.repository.add_revision(None, record, actor=actor, action="create")
            uow.commit()
        return record

    def update(self, record_id: str, data: Mapping[str, object], *, expected_version: int, actor: str) -> dict:
        actor = self._actor(actor)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 1:
            raise CostingValidationError("请提供当前成本单版本。", "version")
        if not isinstance(data, Mapping):
            raise CostingValidationError("成本单格式无效。")
        with self._uow(True) as uow:
            before = uow.repository.get(record_id)
            if before is None:
                raise CostingNotFound()
            if before["version"] != expected_version:
                raise CostingConflict("成本单已被其他操作修改。请保留当前输入，重新打开最新成本单后核对。")
            merged = {**before, **data, "source_info": before["source_info"]}
            record = calculate_cost(merged, previous=before)
            prior_items = {item["id"]: item for item in before["items"]}
            used_ids: set[str] = set()
            for item in record["items"]:
                item_id = item["id"]
                prior = prior_items.get(item_id)
                if prior is not None and item_id not in used_ids:
                    item["source_info"] = prior.get("source_info", {})
                    # Older editors omit columns introduced since their page was loaded.
                    for field in ("product_code", "material_attribute"):
                        if field not in item and field in prior:
                            item[field] = prior[field]
                    used_ids.add(item_id)
                else:
                    item["id"] = uuid4().hex
                    item["source_info"] = {}
                    item.setdefault("material_attribute", DEFAULT_MATERIAL_ATTRIBUTE)
            record.update(id=before["id"], version=before["version"] + 1, created_at=before["created_at"],
                          updated_at=_timestamp(), created_by=before["created_by"], updated_by=actor,
                          source=before["source"])
            display = with_pricing(record)
            if not uow.repository.update(record, expected_version=expected_version):
                raise CostingConflict()
            uow.repository.add_revision(before, record, actor=actor, action="update")
            uow.commit()
        return display

    def revisions(self, record_id: str) -> list[dict]:
        with self._uow(False) as uow:
            if uow.repository.get(record_id) is None:
                raise CostingNotFound()
            return uow.repository.revisions(record_id)

    def preview_line_codes(self) -> dict:
        with self._uow(False) as uow:
            result, _updates = plan_line_codes(uow.repository)
            return result

    def apply_line_codes(self, digest: str, *, actor: str) -> dict:
        actor = self._actor(actor)
        if not isinstance(digest, str) or len(digest) != 64:
            raise CostingValidationError("请先预览本次整理结果。")
        with self._uow(True) as uow:
            result, updates = plan_line_codes(uow.repository)
            if not updates:
                return result
            if result["digest"] != digest:
                raise CostingConflict("成本资料已变化，请核对最新预览后重新应用。")
            for before, after in updates:
                version = before["version"]
                after.update(version=version + 1, updated_at=_timestamp(), updated_by=actor)
                if not uow.repository.update(after, expected_version=version):
                    raise CostingConflict("成本资料已变化，请重新预览。")
                uow.repository.add_revision(before, after, actor=actor, action="update")
            uow.commit()
        return result

    def references(self) -> dict:
        with self._uow(False) as uow:
            return uow.repository.references()

    def import_records(self, records: list[dict], *, actor: str, references: dict | None = None) -> dict:
        actor = self._actor(actor)
        if not isinstance(records, list) or not records or len(records) > 5000:
            raise CostingValidationError("一次导入需要 1 至 5000 张成本单。")
        prepared = [self._new_record(data, actor, "excel") for data in records]
        codes = [record["code"] for record in prepared]
        if len(set(codes)) != len(codes):
            raise CostingConflict("导入数据中存在重复型号，请先核对。")
        normalized_references = reference_snapshot(references) if references is not None else None
        with self._uow(True) as uow:
            if normalized_references is not None:
                uow.repository.save_references(normalized_references)
            for record in prepared:
                if uow.repository.find_code(record["code"]) is not None:
                    raise CostingConflict(f"型号 {record['code']} 已有成本单，本次导入未写入任何数据。")
                uow.repository.insert(record)
                uow.repository.add_revision(None, record, actor=actor, action="import")
            uow.commit()
        return {"imported": len(prepared), "records": prepared}
