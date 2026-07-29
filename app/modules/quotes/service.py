from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .domain import (
    QUOTE_CURRENCIES,
    QUOTE_MAX_PRICE,
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
    ContractDocumentDirectoryPort,
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
        contract_document_directory: ContractDocumentDirectoryPort | None = None,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.import_port = import_port
        self.import_lock_port = import_lock_port
        self.product_catalog = product_catalog
        self.customer_directory = customer_directory
        self.contract_document_directory = contract_document_directory

    def _validate_product(self, bld_no: str) -> None:
        if not self.product_catalog.exists(bld_no):
            raise QuoteValidationError(
                "quote.bld_unknown",
                f"产品目录中不存在 BLD 号 {bld_no}，请先在产品目录中新增。",
                field="bld_no",
            )

    def _validate_customer(self, customer_name: str) -> int | None:
        if not self.customer_directory.exists(customer_name):
            raise QuoteValidationError(
                "quote.customer_unknown",
                f"客户 {customer_name} 未登记，请先在客户列表中新增。",
                field="customer_name",
            )
        resolver = getattr(self.customer_directory, "find_id", None)
        return resolver(customer_name) if callable(resolver) else None

    def _validate_targets(self, *, bld_no: str, customer_name: str) -> int | None:
        self._validate_product(bld_no)
        return self._validate_customer(customer_name)

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

    def filter_options(self, filters: Mapping[str, object] | QuoteFilters) -> dict[str, list[dict[str, object]]]:
        normalized = filters if isinstance(filters, QuoteFilters) else build_quote_filters(filters)
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.filter_options(normalized)

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
            customer_id = self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
            draft = replace(draft, customer_id=customer_id)
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
                    customer_id = self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
                    draft = replace(draft, customer_id=customer_id)
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

    def sales_contract_draft(
        self,
        quote_no: object,
        quote_ids: Sequence[object],
        language: object,
    ) -> dict[str, object]:
        number = compact_text(quote_no)
        language_code = compact_text(language)
        if not language_code:
            raise QuoteValidationError(
                "quote.contract_language_required",
                "请选择销售合同语言版本。",
                field="language",
            )
        if language_code == "en-US":
            raise QuoteValidationError(
                "quote.contract_language_unavailable",
                "英文版销售合同暂未开放。",
                field="language",
            )
        if language_code != "zh-CN":
            raise QuoteValidationError(
                "quote.contract_language_invalid",
                "销售合同语言版本无效。",
                field="language",
            )
        if not number:
            raise QuoteValidationError("quote.contract_quote_required", "报价单号不能为空。", field="source_quote_no")

        selected_ids: list[int] = []
        for value in quote_ids:
            text = compact_text(value)
            if not text.isdigit():
                raise QuoteValidationError("quote.contract_selection_invalid", "报价明细选择无效。", field="quote_id")
            quote_id = int(text)
            if quote_id not in selected_ids:
                selected_ids.append(quote_id)
        if not selected_ids:
            raise QuoteValidationError(
                "quote.contract_selection_required",
                "请至少选择一条报价明细。",
                field="quote_id",
            )

        with self.unit_of_work_factory() as unit_of_work:
            records = unit_of_work.repository.list_by_quote_no(number)
            selected = [record for record in records if record.id in selected_ids]
            if len(selected) != len(selected_ids):
                raise QuoteValidationError(
                    "quote.contract_selection_mismatch",
                    "所选报价明细不属于当前报价单，请刷新后重试。",
                    field="quote_id",
                )
            customer_ids = {record.customer_id for record in selected if record.customer_id is not None}
            customer_names = {record.customer_name.strip().casefold() for record in selected}
            currencies = {record.currency.strip().upper() for record in selected}
            if len(customer_names) != 1 or len(customer_ids) > 1:
                raise QuoteValidationError(
                    "quote.contract_customer_mismatch",
                    "所选报价明细必须属于同一个客户。",
                    field="quote_id",
                )
            if len(currencies) != 1:
                raise QuoteValidationError(
                    "quote.contract_currency_mismatch",
                    "所选报价明细必须使用同一种币种。",
                    field="quote_id",
                )
            customer_id = next(iter(customer_ids), None)

        customer_id = self.customer_directory.find_active_id(customer_id, selected[0].customer_name)
        if customer_id is None:
            raise QuoteValidationError(
                "quote.contract_customer_inactive",
                f"客户 {selected[0].customer_name} 已停用或不存在，不能生成新销售合同。",
                field="quote_id",
            )

        items: list[dict[str, object]] = []
        price_kinds: set[str] = set()
        for record in selected:
            if record.tax_price is not None and record.net_price is not None:
                raise QuoteValidationError(
                    "quote.contract_price_ambiguous",
                    f"报价明细 {record.bld_no} 同时存在含税价和不含税价，无法判断合同应采用的原报价口径。",
                    field="quote_id",
                )
            if record.tax_price is not None:
                price = record.tax_price
                price_kind = "tax"
            elif record.net_price is not None:
                price = record.net_price
                price_kind = "net"
            else:
                raise QuoteValidationError(
                    "quote.contract_price_required",
                    f"报价明细 {record.bld_no} 缺少有效价格。",
                    field="quote_id",
                )
            if not math.isfinite(price):
                raise QuoteValidationError(
                    "quote.contract_price_invalid",
                    f"报价明细 {record.bld_no} 的价格必须是有限数字。",
                    field="quote_id",
                )
            if price < 0:
                raise QuoteValidationError(
                    "quote.contract_price_invalid",
                    f"报价明细 {record.bld_no} 的价格不能为负数。",
                    field="quote_id",
                )
            if price > QUOTE_MAX_PRICE:
                raise QuoteValidationError(
                    "quote.contract_price_invalid",
                    f"报价明细 {record.bld_no} 的价格数值过大。",
                    field="quote_id",
                )
            price_kinds.add(price_kind)
            items.append(
                {
                    "quote_id": record.id,
                    "quote_version": record.version,
                    "product_code": record.bld_no or record.product_model,
                    "customer_code": record.customer_product_code,
                    "oe_no": "",
                    "product_name": "",
                    "models": "",
                    "quantity": "",
                    "unit_price": f"{price:.4f}",
                    "price_kind": price_kind,
                    "delivery_date": "",
                    "note": record.remark,
                }
            )
        price_basis = next(iter(price_kinds)) if len(price_kinds) == 1 else "mixed"
        return {
            "source_quote_no": number,
            "quote_ids": selected_ids,
            "language": language_code,
            "currency": next(iter(currencies)),
            "customer_id": customer_id,
            "customer_name": selected[0].customer_name,
            "price_basis": price_basis,
            "items": items,
        }

    def contract_documents_by_quote_no(self, quote_no: object) -> list[dict[str, object]]:
        number = compact_text(quote_no)
        if not number or self.contract_document_directory is None:
            return []
        return self.contract_document_directory.list_by_quote_no(number)

    def customer_summaries(self, customers: Sequence[tuple[int, str]]) -> dict[int, dict[str, object]]:
        if not customers:
            return {}
        with self.unit_of_work_factory() as unit_of_work:
            return {
                int(customer_id): unit_of_work.repository.customer_summary(int(customer_id), str(customer_name))
                for customer_id, customer_name in customers
            }

    def customer_quote_history(
        self,
        customer_id: int,
        customer_name: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.repository.customer_history(
                int(customer_id),
                str(customer_name),
                limit=max(1, min(200, int(limit))),
            )

    def rename_customer_references(self, customer_id: int, old_name: object, new_name: object) -> int:
        previous = clean_multiline(old_name)
        replacement = clean_multiline(new_name)
        if not previous or not replacement:
            raise QuoteValidationError(
                "quote.customer_rename_invalid",
                "客户改名前后的名称不能为空。",
                field="customer_name",
            )
        with self.unit_of_work_factory() as unit_of_work:
            updated = unit_of_work.repository.rename_customer_references(
                int(customer_id),
                previous,
                replacement,
            )
            unit_of_work.commit()
        return updated

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
            self._validate_product(draft.bld_no)
            same_customer = clean_multiline(draft.customer_name).casefold() == clean_multiline(
                before.customer_name
            ).casefold()
            if same_customer:
                draft = replace(
                    draft,
                    customer_id=before.customer_id,
                    customer_name=before.customer_name,
                )
            else:
                draft = replace(
                    draft,
                    customer_id=self._validate_customer(draft.customer_name),
                )
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
                    customer_id = self._validate_targets(bld_no=draft.bld_no, customer_name=draft.customer_name)
                    draft = replace(draft, customer_id=customer_id)
                except QuoteValidationError:
                    skipped += 1
                    continue
                record = unit_of_work.repository.add(draft)
                unit_of_work.repository.audit("新增报价记录", record, actor=actor)
                imported += 1
            unit_of_work.commit()
        return imported, skipped
