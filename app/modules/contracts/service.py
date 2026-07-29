from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import cast

from app.drawings import safe_filename_part
from app.helpers import unique_prefixed_path

from .document_defaults import (
    DEFAULT_BUYER_NAME,
    DEFAULT_DELIVERY_ADDRESS,
    DEFAULT_PAYMENT_TERMS,
    DEFAULT_PRICE_NOTE,
    DEFAULT_QUALITY_TERMS,
    DEFAULT_SALES_PAYMENT_TERMS,
    DEFAULT_SALES_PRICE_NOTE,
    DEFAULT_SALES_QUALITY_TERMS,
)
from .document_registry import ContractDocumentRegistry
from .form_parser import (
    default_contract_no,
    default_sales_contract_no,
    purchase_contract_from_form,
    sales_contract_from_form,
)
from .ports import ContractCustomerDirectoryPort, QuoteSalesContractSourcePort, QuoteSelectionTokenPort
from .quote_contract_source import QuoteContractSource


CONTRACT_HISTORY_LIMIT = 200


class ContractService:
    def __init__(
        self,
        unit_of_work_factory,
        product_service,
        pdf_adapter,
        image_resolver,
        quote_source: QuoteSalesContractSourcePort | None = None,
        selection_token: QuoteSelectionTokenPort | None = None,
        *,
        customer_directory: ContractCustomerDirectoryPort | None = None,
        document_root: Path | None = None,
    ) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.product_service = product_service
        self.pdf_adapter = pdf_adapter
        self.image_resolver = image_resolver
        self.quote_contract_source = QuoteContractSource(quote_source, selection_token, product_service)
        self.customer_directory = customer_directory
        self.document_root = document_root
        self.document_registry = ContractDocumentRegistry(unit_of_work_factory, document_root)

    def page_context(
        self,
        *,
        mode: str,
        user_label: str,
        output_reader,
        history_type: str,
        history_query: str,
        source_quote_no: str = "",
        quote_ids: Sequence[object] = (),
        language: str = "",
    ) -> dict[str, object]:
        is_sales = mode == "sales"
        contract_draft = (
            self.quote_contract_source.build_draft(
                source_quote_no=source_quote_no,
                quote_ids=quote_ids,
                language=language,
            )
            if is_sales and source_quote_no
            else self._empty_sales_contract_draft()
        )
        if contract_draft.get("source_quote_no"):
            contract_draft["source_token"] = self.quote_contract_source.sign_selection(contract_draft)
        draft_items = contract_draft.get("items", [])
        if not isinstance(draft_items, list):
            raise ValueError("合同草稿明细无效。")
        contract_rows = [dict(item) for item in draft_items]
        minimum_rows = 0 if contract_draft.get("source_quote_no") else 3
        while len(contract_rows) < minimum_rows:
            contract_rows.append(self._empty_contract_row())
        contract_outputs = self.history(
            output_reader,
            history_type=history_type,
            query=history_query,
        )
        return {
            "contract_mode": mode,
            "default_contract_no": default_sales_contract_no(user_label)
            if is_sales
            else default_contract_no(user_label),
            "default_date": date.today().isoformat(),
            "defaults": {
                "buyer_name": DEFAULT_BUYER_NAME,
                "delivery_address": "" if is_sales else DEFAULT_DELIVERY_ADDRESS,
                "payment_terms": DEFAULT_SALES_PAYMENT_TERMS if is_sales else DEFAULT_PAYMENT_TERMS,
                "price_note": self._sales_price_note(contract_draft) if is_sales else DEFAULT_PRICE_NOTE,
                "quality_terms": DEFAULT_SALES_QUALITY_TERMS if is_sales else DEFAULT_QUALITY_TERMS,
            },
            "contract_outputs": contract_outputs,
            "contract_filters": {
                "contract_type": history_type if history_type in {"all", "purchase", "sales"} else "all",
                "contract_q": history_query.strip(),
            },
            "contract_draft": contract_draft,
            "contract_rows": contract_rows,
        }

    def lookup_product(self, bld_no: str) -> dict[str, object] | None:
        key = bld_no.strip()
        if not key:
            return None
        product = self.product_service.find_by_bld(key)
        return product.web_payload() if product is not None else None

    @staticmethod
    def _empty_contract_row() -> dict[str, object]:
        return {
            "quote_id": "",
            "quote_version": "",
            "product_code": "",
            "customer_code": "",
            "oe_no": "",
            "product_name": "",
            "models": "",
            "quantity": "",
            "unit_price": "",
            "price_kind": "",
            "delivery_date": "",
            "note": "",
        }

    @staticmethod
    def _empty_sales_contract_draft() -> dict[str, object]:
        return {
            "source_quote_no": "",
            "quote_ids": [],
            "language": "zh-CN",
            "currency": "CNY",
            "customer_id": None,
            "customer_name": "",
            "price_basis": "tax",
            "items": [],
            "source_token": "",
        }

    @staticmethod
    def _sales_price_note(contract_draft: Mapping[str, object]) -> str:
        if not contract_draft.get("source_quote_no"):
            return DEFAULT_SALES_PRICE_NOTE
        currency = str(contract_draft.get("currency") or "CNY")
        price_basis = str(contract_draft.get("price_basis") or "mixed")
        if price_basis == "tax":
            return f"以上单价按原报价含税价带入，币种为 {currency}；包装费、运费及交货条件以双方最终确认为准。"
        if price_basis == "net":
            return f"以上单价按原报价不含税价带入，币种为 {currency}；税费、包装费、运费及交货条件以双方最终确认为准。"
        return f"以上单价按各明细原报价口径带入，币种为 {currency}；税费及其他费用以双方最终确认为准。"

    def generate(
        self,
        kind: str,
        form: Mapping[str, object],
        *,
        output_root: Path,
        actor: str,
    ) -> Path:
        source_snapshot_json = ""
        source_snapshot_sha256 = ""
        source_controlled = False
        if kind == "sales":
            source_quote_no = str(form.get("source_quote_no") or "").strip()
            source_controlled = bool(source_quote_no)
            contract = sales_contract_from_form(
                form,
                source_controlled=source_controlled,
                require_language=source_controlled,
            )
            if source_quote_no:
                _, source_snapshot_json, source_snapshot_sha256 = (
                    self.quote_contract_source.validate_and_apply(
                        contract,
                        source_quote_no=source_quote_no,
                        source_token=form.get("source_quote_token"),
                    )
                )
            elif self.customer_directory is not None:
                contract["customer_id"] = self.customer_directory.find_active_id(
                    None,
                    str(contract["customer_name"]),
                )
            party = str(contract["customer_name"])
            folder_kind = "销售合同"
            target_type = "sales_contract"
            action = "生成销售合同"
            fallback = "sales-contract"
        else:
            contract = purchase_contract_from_form(form)
            party = str(contract["supplier_name"])
            folder_kind = "采购合同"
            target_type = "purchase_contract"
            action = "生成采购合同"
            fallback = "purchase-contract"
        party_folder = safe_filename_part(party, "customer" if kind == "sales" else "supplier")
        filename_stem = safe_filename_part(f"{contract['contract_no']}{party}", fallback)
        output_path = unique_prefixed_path(output_root / folder_kind / party_folder, f"{filename_stem}.pdf")
        try:
            with self.unit_of_work_factory() as unit_of_work:
                self._apply_catalog_values(contract, source_controlled=source_controlled)
                self.pdf_adapter.generate(kind, contract, output_path)
                if kind == "sales":
                    unit_of_work.repository.add_document(
                        contract_type="sales",
                        contract_no=str(contract["contract_no"]),
                        customer_id=cast(int | None, contract.get("customer_id")),
                        customer_name=party,
                        source_quote_no=str(contract.get("source_quote_no") or ""),
                        language=str(contract["language"]),
                        currency=str(contract["currency"]),
                        file_path=self.document_registry.relative_path(output_path, output_root=output_root),
                        source_snapshot_json=source_snapshot_json,
                        source_snapshot_sha256=source_snapshot_sha256,
                        actor=actor,
                    )
                amount_prefix = (
                    "¥" if str(contract.get("currency") or "CNY") == "CNY" else str(contract.get("currency") or "")
                )
                unit_of_work.repository.audit(
                    action,
                    target_type,
                    output_path.name,
                    (
                        f"{party}，{len(contract['items'])} 行，合计 {amount_prefix}{contract['total_amount']}"
                        + (
                            f"，来源 {contract['source_quote_no']}，快照 {source_snapshot_sha256[:12]}"
                            if source_snapshot_sha256
                            else ""
                        )
                    ),
                    actor=actor,
                )
                unit_of_work.commit()
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        return output_path

    def documents_for_quote(self, quote_no: object) -> list[dict[str, object]]:
        return self.document_registry.for_quote(quote_no)

    def documents_for_customer(
        self,
        customer_id: int,
        *,
        customer_name: str = "",
        limit: int = 50,
    ) -> list[dict[str, object]]:
        return self.document_registry.for_customer(customer_id, customer_name=customer_name, limit=limit)

    def document_path(self, document_id: int) -> tuple[Path, dict[str, object]] | None:
        return self.document_registry.path(document_id)

    def history(self, output_reader, *, history_type: str, query: str) -> list[dict[str, object]]:
        normalized_type = history_type if history_type in {"all", "purchase", "sales"} else "all"
        rows: list[dict[str, object]] = []
        if normalized_type in {"all", "purchase"}:
            rows.extend(
                self._history_rows(self._collect_outputs(output_reader, "采购合同/**/*.pdf"), "采购合同", query)
            )
        if normalized_type in {"all", "sales"}:
            rows.extend(
                self._history_rows(self._collect_outputs(output_reader, "销售合同/**/*.pdf"), "销售合同", query)
            )
        return sorted(
            rows,
            key=lambda item: cast(Path, item["path"]).stat().st_mtime,
            reverse=True,
        )[:CONTRACT_HISTORY_LIMIT]

    def _apply_catalog_values(self, contract: dict, *, source_controlled: bool = False) -> None:
        if source_controlled:
            return
        for item in contract["items"]:
            record = self.product_service.find_by_bld(str(item["product_code"]))
            if record is None:
                continue
            product = record.web_payload()
            item["product_code"] = product["bld_no"]
            item["oe_no"] = product.get("oe_no_1") or item.get("oe_no", "")
            item["product_name"] = product.get("item") or item.get("product_name", "")
            item["models"] = product.get("models") or item.get("models", "")
            image_path = self.image_resolver(product)
            item["image_path"] = str(image_path) if image_path else ""

    @staticmethod
    def _collect_outputs(output_reader, pattern: str) -> list[Path]:
        seen: set[Path] = set()
        paths = []
        for path in output_reader(pattern, limit=CONTRACT_HISTORY_LIMIT):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
        return sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)

    @staticmethod
    def _history_rows(paths: list[Path], kind: str, query: str) -> list[dict[str, object]]:
        needle = query.strip().lower()
        rows = []
        for path in paths:
            party = "" if path.parent.name == kind or path.parent.name.startswith("u") else path.parent.name
            operator = "历史文件"
            for parent in path.parents:
                if parent.name.startswith("u") and "-" in parent.name:
                    operator = parent.name.split("-", 1)[1] or parent.name
                    break
            updated_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            haystack = " ".join([kind, path.name, party, operator, updated_at]).lower()
            if needle and needle not in haystack:
                continue
            rows.append(
                {
                    "path": path,
                    "kind": kind,
                    "party": party,
                    "name": path.name,
                    "operator": operator,
                    "updated_at": updated_at,
                }
            )
        return rows
