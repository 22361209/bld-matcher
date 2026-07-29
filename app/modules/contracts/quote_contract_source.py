from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, ROUND_HALF_UP
from typing import cast

from .document_values import (
    MONEY_QUANT,
    _contract_line_amount,
    _contract_total_amount,
    _parse_decimal,
    _rmb_upper,
)
from .ports import QuoteSalesContractSourcePort, QuoteSelectionTokenPort


SELECTION_INVALID = "报价来源选择已失效，请重新打开报价单。"
ROWS_CHANGED = "报价来源明细已变化，请重新打开报价单后再生成合同。"


class QuoteContractSource:
    def __init__(
        self,
        quote_source: QuoteSalesContractSourcePort | None,
        selection_token: QuoteSelectionTokenPort | None,
        product_service,
    ) -> None:
        self.quote_source = quote_source
        self.selection_token = selection_token
        self.product_service = product_service

    def build_draft(
        self,
        *,
        source_quote_no: object,
        quote_ids: Sequence[object],
        language: object,
    ) -> dict[str, object]:
        if self.quote_source is None:
            raise ValueError("报价转销售合同服务暂不可用。")
        draft = self.quote_source.build_draft(source_quote_no, quote_ids, language)
        raw_items = draft.get("items", [])
        if not isinstance(raw_items, list) or any(not isinstance(item, Mapping) for item in raw_items):
            raise ValueError("报价来源明细无效，请重新打开报价单后再生成合同。")
        items = [dict(item) for item in raw_items]
        for item in items:
            record = self.product_service.find_by_bld(str(item.get("product_code") or ""))
            if record is None:
                continue
            product = record.web_payload()
            item["product_code"] = product["bld_no"]
            item["oe_no"] = product.get("oe_no_1") or ""
            item["product_name"] = product.get("item") or ""
            item["models"] = product.get("models") or ""
        return {**draft, "items": items}

    @staticmethod
    def selection_rows(draft: Mapping[str, object]) -> list[dict[str, int]]:
        raw_items = draft.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError(SELECTION_INVALID)
        rows: list[dict[str, int]] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise ValueError(SELECTION_INVALID)
            try:
                quote_id = int(item.get("quote_id") or 0)
                quote_version = int(item.get("quote_version") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(SELECTION_INVALID) from exc
            if quote_id <= 0 or quote_version <= 0:
                raise ValueError(SELECTION_INVALID)
            rows.append({"quote_id": quote_id, "quote_version": quote_version})
        if not rows:
            raise ValueError(SELECTION_INVALID)
        return rows

    def sign_selection(self, draft: Mapping[str, object]) -> str:
        if self.selection_token is None:
            raise ValueError("报价来源选择服务暂不可用。")
        _snapshot_json, source_sha256 = self._source_snapshot(draft)
        return self.selection_token.sign(
            {
                "schema_version": 2,
                "source_quote_no": str(draft.get("source_quote_no") or ""),
                "language": str(draft.get("language") or ""),
                "rows": self.selection_rows(draft),
                "source_sha256": source_sha256,
            }
        )

    def _verify_selection(
        self,
        token: object,
        *,
        source_quote_no: str,
        language: str,
    ) -> tuple[list[dict[str, int]], str]:
        if self.selection_token is None:
            raise ValueError("报价来源选择服务暂不可用。")
        payload = self.selection_token.verify(token)
        if (
            payload.get("schema_version") != 2
            or str(payload.get("source_quote_no") or "") != source_quote_no
            or str(payload.get("language") or "") != language
        ):
            raise ValueError(SELECTION_INVALID)
        raw_rows = payload.get("rows")
        source_sha256 = str(payload.get("source_sha256") or "")
        if (
            not isinstance(raw_rows, list)
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256)
        ):
            raise ValueError(SELECTION_INVALID)
        return self.selection_rows({"items": raw_rows}), source_sha256

    @staticmethod
    def _posted_selection_rows(contract: Mapping[str, object]) -> list[dict[str, int]]:
        items = contract.get("items", [])
        if not isinstance(items, list):
            raise ValueError(ROWS_CHANGED)
        rows: list[dict[str, int]] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError(ROWS_CHANGED)
            quote_id_text = str(item.get("source_quote_id") or "").strip()
            quote_version_text = str(item.get("source_quote_version") or "").strip()
            if not quote_id_text.isdigit() or not quote_version_text.isdigit():
                raise ValueError(ROWS_CHANGED)
            quote_id = int(quote_id_text)
            quote_version = int(quote_version_text)
            if quote_id <= 0 or quote_version <= 0:
                raise ValueError(ROWS_CHANGED)
            rows.append({"quote_id": quote_id, "quote_version": quote_version})
        return rows

    def validate_and_apply(
        self,
        contract: dict[str, object],
        *,
        source_quote_no: str,
        source_token: object,
    ) -> tuple[dict[str, object], str, str]:
        language = str(contract.get("language") or "")
        expected_rows, expected_source_sha256 = self._verify_selection(
            source_token,
            source_quote_no=source_quote_no,
            language=language,
        )
        if self._posted_selection_rows(contract) != expected_rows:
            raise ValueError(ROWS_CHANGED)
        current_draft = self.build_draft(
            source_quote_no=source_quote_no,
            quote_ids=[row["quote_id"] for row in expected_rows],
            language=language,
        )
        current_rows = self.selection_rows(current_draft)
        if [row["quote_id"] for row in current_rows] != [row["quote_id"] for row in expected_rows]:
            raise ValueError(ROWS_CHANGED)
        if current_rows != expected_rows:
            raise ValueError("来源报价已被修订，请重新打开报价单后再生成合同。")
        snapshot_json, snapshot_sha256 = self._source_snapshot(current_draft)
        if snapshot_sha256 != expected_source_sha256:
            raise ValueError("来源报价或产品资料已变化，请重新打开报价单后再生成合同。")
        self._apply_source_items(contract, current_draft)
        return current_draft, snapshot_json, snapshot_sha256

    @staticmethod
    def _apply_source_items(contract: dict[str, object], source_draft: Mapping[str, object]) -> None:
        posted_items = contract.get("items", [])
        source_items = source_draft.get("items", [])
        if (
            not isinstance(posted_items, list)
            or not isinstance(source_items, list)
            or len(posted_items) != len(source_items)
        ):
            raise ValueError(ROWS_CHANGED)
        total = Decimal("0")
        total_quantity = Decimal("0")
        items: list[dict[str, object]] = []
        for index, (posted, source) in enumerate(zip(posted_items, source_items, strict=True), start=1):
            if not isinstance(posted, Mapping) or not isinstance(source, Mapping):
                raise ValueError(ROWS_CHANGED)
            quantity = cast(Decimal, posted["quantity"])
            unit_price = _parse_decimal(source.get("unit_price"), "来源报价价格")
            amount = _contract_line_amount(quantity, unit_price, f"第 {index} 行")
            total = _contract_total_amount(total, amount)
            total_quantity += quantity
            items.append(
                {
                    "source_quote_id": int(source["quote_id"]),
                    "source_quote_version": int(source["quote_version"]),
                    "product_code": str(source.get("product_code") or ""),
                    "customer_code": str(source.get("customer_code") or ""),
                    "oe_no": str(source.get("oe_no") or ""),
                    "product_name": str(source.get("product_name") or ""),
                    "models": str(source.get("models") or ""),
                    "image_path": "",
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "amount": amount,
                    "price_kind": str(source.get("price_kind") or ""),
                    "delivery_date": str(posted.get("delivery_date") or ""),
                    "note": str(source.get("note") or ""),
                }
            )
        currency = str(source_draft.get("currency") or "")
        contract["source_quote_no"] = str(source_draft.get("source_quote_no") or "")
        contract["customer_id"] = source_draft.get("customer_id")
        contract["customer_name"] = str(source_draft.get("customer_name") or "")
        contract["currency"] = currency
        contract["price_basis"] = str(source_draft.get("price_basis") or "")
        contract["items"] = items
        contract["total_amount"] = total.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        contract["total_quantity"] = total_quantity
        contract["total_amount_upper"] = _rmb_upper(contract["total_amount"]) if currency == "CNY" else ""

    @staticmethod
    def _source_snapshot(source_draft: Mapping[str, object]) -> tuple[str, str]:
        source_items = source_draft.get("items", [])
        if not isinstance(source_items, list):
            raise ValueError("报价来源明细无效，请重新打开报价单后再生成合同。")
        rows = []
        for item in source_items:
            if not isinstance(item, Mapping):
                raise ValueError("报价来源明细无效，请重新打开报价单后再生成合同。")
            rows.append(
                {
                    "quote_id": int(item["quote_id"]),
                    "quote_version": int(item["quote_version"]),
                    "bld_no": str(item.get("product_code") or ""),
                    "customer_product_code": str(item.get("customer_code") or ""),
                    "oe_no": str(item.get("oe_no") or ""),
                    "product_name": str(item.get("product_name") or ""),
                    "models": str(item.get("models") or ""),
                    "unit_price": str(item.get("unit_price") or ""),
                    "price_kind": str(item.get("price_kind") or ""),
                    "remark": str(item.get("note") or ""),
                }
            )
        snapshot = {
            "schema_version": 1,
            "source_quote_no": str(source_draft.get("source_quote_no") or ""),
            "customer_id": source_draft.get("customer_id"),
            "customer_name": str(source_draft.get("customer_name") or ""),
            "language": str(source_draft.get("language") or ""),
            "currency": str(source_draft.get("currency") or ""),
            "price_basis": str(source_draft.get("price_basis") or ""),
            "rows": rows,
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()
