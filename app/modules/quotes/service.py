from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .domain import (
    QUOTE_CURRENCIES,
    QuoteFilters,
    QuoteRecord,
    QuoteStats,
    QuoteValidationError,
    build_quote_draft,
    build_quote_filters,
    clean_multiline,
    compact_text,
)
from .ports import ImportLockBusyError, ImportLockPort, QuoteImportPort, QuoteUnitOfWorkFactory


logger = logging.getLogger(__name__)


class QuoteNotFoundError(LookupError):
    def __init__(self, quote_id: int) -> None:
        super().__init__(f"报价记录 {quote_id} 不存在。")
        self.quote_id = quote_id


class QuoteVersionConflictError(RuntimeError):
    def __init__(self, quote_id: int, *, expected_version: int, current_version: int) -> None:
        super().__init__("报价记录已被其他请求修改，请读取最新版本后重试。")
        self.quote_id = quote_id
        self.expected_version = expected_version
        self.current_version = current_version


class QuoteImportError(ValueError):
    pass


class QuoteImportBusyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QuotePage:
    records: list[QuoteRecord]
    total: int
    limit: int
    offset: int


class QuoteService:
    def __init__(
        self,
        unit_of_work_factory: QuoteUnitOfWorkFactory,
        import_port: QuoteImportPort,
        import_lock_port: ImportLockPort,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.import_port = import_port
        self.import_lock_port = import_lock_port

    def list_records(
        self,
        filters: Mapping[str, object] | QuoteFilters,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> QuotePage:
        normalized = filters if isinstance(filters, QuoteFilters) else build_quote_filters(filters)
        safe_limit = max(1, min(500, int(limit)))
        safe_offset = max(0, int(offset))
        with self.unit_of_work_factory() as unit_of_work:
            total = unit_of_work.repository.count(normalized)
            records = unit_of_work.repository.list(normalized, limit=safe_limit, offset=safe_offset)
        return QuotePage(records=records, total=total, limit=safe_limit, offset=safe_offset)

    def get_record(self, quote_id: int) -> QuoteRecord:
        with self.unit_of_work_factory() as unit_of_work:
            record = unit_of_work.repository.get(quote_id)
        if record is None:
            raise QuoteNotFoundError(quote_id)
        return record

    def latest(self, *, customer_name: object, bld_no: object) -> QuoteRecord | None:
        customer = clean_multiline(customer_name)
        product = compact_text(bld_no)
        if not customer or not product:
            raise QuoteValidationError(
                "quote.latest_filters_required",
                "customer_name 和 bld_no 不能为空。",
            )
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.latest(customer_name=customer, bld_no=product)

    def stats(self) -> QuoteStats:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.stats()

    def create(self, data: Mapping[str, object], *, actor: str) -> QuoteRecord:
        draft = build_quote_draft(data, actor=actor)
        with self.unit_of_work_factory() as unit_of_work:
            record = unit_of_work.repository.add(draft)
            unit_of_work.repository.audit("新增报价记录", record, actor=actor)
            unit_of_work.commit()
        return record

    def update(
        self,
        quote_id: int,
        data: Mapping[str, object],
        *,
        actor: str,
        expected_version: int | None = None,
    ) -> QuoteRecord:
        with self.unit_of_work_factory() as unit_of_work:
            before = unit_of_work.repository.get(quote_id)
            if before is None:
                raise QuoteNotFoundError(quote_id)
            if expected_version is not None and expected_version != before.version:
                raise QuoteVersionConflictError(
                    quote_id,
                    expected_version=expected_version,
                    current_version=before.version,
                )
            draft = build_quote_draft(data, actor=actor, existing=before)
            after = unit_of_work.repository.update(
                quote_id,
                draft,
                expected_version=before.version,
            )
            if after is None:
                current = unit_of_work.repository.get(quote_id)
                raise QuoteVersionConflictError(
                    quote_id,
                    expected_version=before.version,
                    current_version=current.version if current else before.version + 1,
                )
            if before.legacy_payload() != after.legacy_payload():
                unit_of_work.repository.add_revision(before, after, actor=actor)
                unit_of_work.repository.audit("修正报价记录", after, actor=actor)
            unit_of_work.commit()
        return after

    def preview_import(self, path: Path, *, customer_name: object, currency: object) -> dict:
        customer = clean_multiline(customer_name)
        currency_code = compact_text(currency).upper()
        if not customer:
            raise QuoteValidationError("quote.customer_required", "请填写客户名称。", field="customer_name")
        if currency_code not in QUOTE_CURRENCIES:
            raise QuoteValidationError("quote.invalid_currency", "请选择币种。", field="currency")
        try:
            return self.import_port.parse(path, customer_name=customer, currency=currency_code)
        except QuoteValidationError:
            raise
        except Exception as exc:
            logger.exception("Quote import preview parsing failed")
            raise QuoteImportError("无法解析报价文件，请检查文件内容和格式。") from exc

    def encode_import_rows(self, rows: list[dict]) -> str:
        return self.import_port.encode(rows)

    def apply_import_payload(self, payload: str, *, actor: str) -> tuple[int, int]:
        try:
            rows = self.import_port.decode(payload)
        except Exception as exc:
            logger.info("Quote import payload decode failed", exc_info=True)
            raise QuoteImportError("导入数据无法解码或已过期。") from exc
        try:
            with self.import_lock_port(actor, "报价记录批量导入"):
                return self._apply_import_rows(rows, actor=actor)
        except ImportLockBusyError as exc:
            raise QuoteImportBusyError(str(exc)) from exc

    def _apply_import_rows(self, rows: list[dict], *, actor: str) -> tuple[int, int]:
        imported = 0
        skipped = 0
        with self.unit_of_work_factory() as unit_of_work:
            for row in rows:
                if row.get("status") != "valid":
                    skipped += 1
                    continue
                draft = build_quote_draft(row, actor=actor)
                record = unit_of_work.repository.add(draft)
                unit_of_work.repository.audit("新增报价记录", record, actor=actor)
                imported += 1
            unit_of_work.commit()
        return imported, skipped
