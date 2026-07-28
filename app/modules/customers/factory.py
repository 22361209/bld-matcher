from __future__ import annotations

from functools import lru_cache

from app.config import DB_PATH
from app.database import connect

from .service import CustomerService


@lru_cache(maxsize=1)
def get_customer_service() -> CustomerService:
    return CustomerService(lambda: connect(DB_PATH))
