from __future__ import annotations

from functools import lru_cache

from app.config import DB_PATH
from app.database import connect

from .infrastructure import ContractCustomerReader, QuoteCustomerReader
from .service import CustomerService


def _active_owner(username: str) -> bool:
    from app.modules.admin.factory import get_admin_service

    users, _ = get_admin_service().users()
    return any(str(user.get("username") or "") == username and bool(user.get("active")) for user in users)


@lru_cache(maxsize=1)
def get_customer_service() -> CustomerService:
    def connection_factory():
        return connect(DB_PATH)

    return CustomerService(
        connection_factory,
        QuoteCustomerReader(),
        _active_owner,
        ContractCustomerReader(),
    )
