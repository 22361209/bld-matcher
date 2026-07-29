from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .domain import CustomerQuoteSummary


class QuoteCustomerReader:
    def __init__(self, service_factory: Callable[[], Any] | None = None) -> None:
        self.service_factory = service_factory

    def _service(self):
        if self.service_factory is not None:
            return self.service_factory()
        from app.modules.quotes.factory import get_quote_service

        return get_quote_service()

    def quote_stats(self, customers: list[tuple[int, str]]) -> dict[int, dict[str, object]]:
        return self._service().customer_summaries(customers)

    def quote_history(self, customer_id: int, customer_name: str, *, limit: int = 50) -> list[CustomerQuoteSummary]:
        rows = self._service().customer_quote_history(
            customer_id,
            customer_name,
            limit=limit,
        )
        return [
            CustomerQuoteSummary(
                quote_no=str(row.get("quote_no") or ""),
                quote_date=str(row.get("quote_date") or ""),
                line_count=int(row.get("line_count") or 0),
                product_count=int(row.get("product_count") or 0),
                currency=str(row.get("currency") or ""),
                quoted_by=str(row.get("quoted_by") or ""),
            )
            for row in rows
        ]

    def rename_customer_references(self, customer_id: int, old_name: str, new_name: str) -> int:
        return self._service().rename_customer_references(customer_id, old_name, new_name)


class ContractCustomerReader:
    def documents(self, customer_id: int, customer_name: str, *, limit: int = 50) -> list[dict[str, object]]:
        from app.modules.contracts.factory import get_contract_service

        return get_contract_service().documents_for_customer(
            customer_id,
            customer_name=customer_name,
            limit=limit,
        )
