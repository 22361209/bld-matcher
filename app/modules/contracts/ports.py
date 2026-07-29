from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol


class QuoteSalesContractSourcePort(Protocol):
    def build_draft(
        self,
        quote_no: object,
        quote_ids: Sequence[object],
        language: object,
    ) -> dict[str, object]: ...


class QuoteSelectionTokenPort(Protocol):
    def sign(self, payload: Mapping[str, object]) -> str: ...

    def verify(self, token: object) -> dict[str, object]: ...


class ContractCustomerDirectoryPort(Protocol):
    def find_active_id(self, customer_id: int | None, customer_name: str) -> int | None: ...
