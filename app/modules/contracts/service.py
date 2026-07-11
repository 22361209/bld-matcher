from __future__ import annotations

from collections.abc import Mapping
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
from .form_parser import (
    default_contract_no,
    default_sales_contract_no,
    purchase_contract_from_form,
    sales_contract_from_form,
)


CONTRACT_HISTORY_LIMIT = 200


class ContractService:
    def __init__(self, unit_of_work_factory, product_service, pdf_adapter, image_resolver) -> None:
        self.unit_of_work_factory = unit_of_work_factory
        self.product_service = product_service
        self.pdf_adapter = pdf_adapter
        self.image_resolver = image_resolver

    def page_context(
        self,
        *,
        mode: str,
        user_label: str,
        output_reader,
        history_type: str,
        history_query: str,
    ) -> dict[str, object]:
        is_sales = mode == "sales"
        contract_outputs = self.history(
            output_reader,
            history_type=history_type,
            query=history_query,
        )
        return {
            "contract_mode": mode,
            "default_contract_no": default_sales_contract_no(user_label) if is_sales else default_contract_no(user_label),
            "default_date": date.today().isoformat(),
            "defaults": {
                "buyer_name": DEFAULT_BUYER_NAME,
                "delivery_address": "" if is_sales else DEFAULT_DELIVERY_ADDRESS,
                "payment_terms": DEFAULT_SALES_PAYMENT_TERMS if is_sales else DEFAULT_PAYMENT_TERMS,
                "price_note": DEFAULT_SALES_PRICE_NOTE if is_sales else DEFAULT_PRICE_NOTE,
                "quality_terms": DEFAULT_SALES_QUALITY_TERMS if is_sales else DEFAULT_QUALITY_TERMS,
            },
            "contract_outputs": contract_outputs,
            "contract_filters": {
                "contract_type": history_type if history_type in {"all", "purchase", "sales"} else "all",
                "contract_q": history_query.strip(),
            },
        }

    def lookup_product(self, bld_no: str) -> dict[str, object] | None:
        key = bld_no.strip()
        if not key:
            return None
        product = self.product_service.find_by_bld(key)
        return product.web_payload() if product is not None else None

    def generate(
        self,
        kind: str,
        form: Mapping[str, object],
        *,
        output_root: Path,
        actor: str,
    ) -> Path:
        if kind == "sales":
            contract = sales_contract_from_form(form)
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
                self._apply_catalog_values(contract)
                self.pdf_adapter.generate(kind, contract, output_path)
                unit_of_work.repository.audit(
                    action,
                    target_type,
                    output_path.name,
                    f"{party}，{len(contract['items'])} 行，合计 ¥{contract['total_amount']}",
                    actor=actor,
                )
                unit_of_work.commit()
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        return output_path

    def history(self, output_reader, *, history_type: str, query: str) -> list[dict[str, object]]:
        normalized_type = history_type if history_type in {"all", "purchase", "sales"} else "all"
        rows: list[dict[str, object]] = []
        if normalized_type in {"all", "purchase"}:
            rows.extend(self._history_rows(self._collect_outputs(output_reader, "采购合同/**/*.pdf"), "采购合同", query))
        if normalized_type in {"all", "sales"}:
            rows.extend(self._history_rows(self._collect_outputs(output_reader, "销售合同/**/*.pdf"), "销售合同", query))
        return sorted(
            rows,
            key=lambda item: cast(Path, item["path"]).stat().st_mtime,
            reverse=True,
        )[:CONTRACT_HISTORY_LIMIT]

    def _apply_catalog_values(self, contract: dict) -> None:
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
