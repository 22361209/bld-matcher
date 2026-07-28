from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
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
from .ports import (
    CustomerDirectoryPort,
    ImportLockBusyError,
    ImportLockPort,
    ProductCatalogPort,
    QuoteImportPort,
    QuoteUnitOfWorkFactory,
)


logger = logging.getLogger(__name__)
SYSTEM_MANAGED_QUOTE_FIELDS = frozenset({"quoted_by", "source_type", "quote_no"})


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
        product_catalog: ProductCatalogPort,
        customer_directory: CustomerDirectoryPort,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.import_port = import_port
        self.import_lock_port = import_lock_port
        self.product_catalog = product_catalog
        self.customer_directory = customer_directory

    def _validate_targets(self, *, bld_no: str, customer_name: str) -> None:
        if not self.product_catalog.exists(bld_no):
            raise QuoteValidationError(
                "quote.bld_unknown",
                f"产品目录中不存在 BLD 号 {bld_no}，请先在产品目录中新增。",
                field="bld_no",
            )
        if not self.customer_directory.exists(customer_name):
            raise QuoteValidationError(
                "quote.customer_unknown",
                f"客户 {customer_name} 未登记，请先在客户列表中新增。",
                field="customer_name",
            )

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
        with self.unit_of_work_factory() as unit_of_work:
            quote_no = compact_text(data.get("quote_no")) or unit_of_work.repository.next_quote_no(
                datetime.now().strftime("%y%m%d")
            )
            draft = build_quote_draft({**data, "quote_no": quote_no}, actor=actor)
            self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
            record = unit_of_work.repository.add(draft)
            unit_of_work.repository.audit("新增报价记录", record, actor=actor)
            unit_of_work.commit()
        return record

    def create_many(
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        actor: str,
    ) -> tuple[list[QuoteRecord], int, str]:
        written: list[QuoteRecord] = []
        skipped = 0
        quote_no = ""
        with self.unit_of_work_factory() as unit_of_work:
            quote_no = unit_of_work.repository.next_quote_no(datetime.now().strftime("%y%m%d"))
            for data in rows:
                try:
                    draft = build_quote_draft({**data, "quote_no": quote_no}, actor=actor)
                    self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
                except QuoteValidationError:
                    skipped += 1
                    continue
                record = unit_of_work.repository.add(draft)
                unit_of_work.repository.audit("新增报价记录", record, actor=actor)
                written.append(record)
            if written:
                unit_of_work.commit()
        return written, skipped, quote_no

    def records_by_quote_no(self, quote_no: object) -> list[QuoteRecord]:
        number = compact_text(quote_no)
        if not number:
            return []
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.list_by_quote_no(number)

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
            immutable_fields = SYSTEM_MANAGED_QUOTE_FIELDS.intersection(data)
            if immutable_fields:
                raise QuoteValidationError(
                    "quote.system_fields_immutable",
                    "quoted_by、source_type 和 quote_no 由系统维护，不能修改。",
                    field=sorted(immutable_fields)[0],
                )
            draft = build_quote_draft(data, actor=actor, existing=before)
            self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
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
            if before.payload() != after.payload():
                unit_of_work.repository.add_revision(before, after, actor=actor)
                unit_of_work.repository.audit("修正报价记录", after, actor=actor)
            unit_of_work.commit()
        return after

    def delete(self, quote_id: int, *, actor: str) -> QuoteRecord:
        with self.unit_of_work_factory() as unit_of_work:
            record = unit_of_work.repository.delete(quote_id)
            if record is None:
                raise QuoteNotFoundError(quote_id)
            unit_of_work.repository.audit("删除报价记录", record, actor=actor)
            unit_of_work.commit()
        return record

    def preview_import(self, path: Path, *, customer_name: object, currency: object) -> dict:
        customer = clean_multiline(customer_name)
        currency_code = compact_text(currency).upper()
        if not customer:
            raise QuoteValidationError("quote.customer_required", "请填写客户名称。", field="customer_name")
        if currency_code not in QUOTE_CURRENCIES:
            raise QuoteValidationError("quote.invalid_currency", "请选择币种。", field="currency")
        if not self.customer_directory.exists(customer):
            raise QuoteValidationError(
                "quote.customer_unknown",
                f"客户 {customer} 未登记，请先在客户列表中新增。",
                field="customer_name",
            )
        try:
            preview = self.import_port.parse(path, customer_name=customer, currency=currency_code)
        except QuoteValidationError:
            raise
        except Exception as exc:
            logger.exception("Quote import preview parsing failed")
            raise QuoteImportError("无法解析报价文件，请检查文件内容和格式。") from exc
        for row in preview.get("rows", []):
            if row.get("status") != "valid":
                continue
            bld_no = str(row.get("bld_no") or "").strip()
            if self.product_catalog.exists(bld_no):
                continue
            row["status"] = "invalid"
            row["error"] = "；".join(
                part for part in (str(row.get("error") or ""), f"产品目录中不存在 BLD 号 {bld_no}") if part
            )
            preview["counts"]["valid"] -= 1
            preview["counts"]["invalid"] += 1
        return preview

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
            quote_no = ""
            for row in rows:
                if row.get("status") != "valid":
                    skipped += 1
                    continue
                if not quote_no:
                    quote_no = unit_of_work.repository.next_quote_no(datetime.now().strftime("%y%m%d"))
                values = dict(row)
                values.update(
                    {
                        "quoted_by": actor,
                        "source_type": "excel",
                        "source_text": "",
                        "attachment_path": "",
                        "quote_no": quote_no,
                    }
                )
                try:
                    draft = build_quote_draft(values, actor=actor)
                    self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
                except QuoteValidationError:
                    skipped += 1
                    continue
                record = unit_of_work.repository.add(draft)
                unit_of_work.repository.audit("新增报价记录", record, actor=actor)
                imported += 1
            unit_of_work.commit()
        return imported, skipped
