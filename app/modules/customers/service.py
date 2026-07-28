from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from app.platform.audit_store import log_event
from app.platform.sync_identity import stable_sync_id

from . import repository
from .domain import Customer, CustomerValidationError, clean_customer_name


ConnectionFactory = Callable[[], AbstractContextManager[Any]]


def customer_sync_id(name: str) -> str:
    # 同一规范化名称在任何设备上生成相同 sync_id，业务同步据此识别同一客户；
    # 改名不改 sync_id，同步时按“更新名称”合并。
    return stable_sync_id("customer", name.upper(), 1)


class CustomerService:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self.connection_factory = connection_factory

    def list(self) -> list[Customer]:
        with self.connection_factory() as connection:
            return repository.list_customers(connection)

    def lookup(self, query: object, *, limit: int = 20) -> list[Customer]:
        text = clean_customer_name(query)
        with self.connection_factory() as connection:
            if not text:
                return repository.list_customers(connection)[:limit]
            return repository.lookup_customers(connection, text, limit=limit)

    def find_by_name(self, name: object) -> Customer | None:
        text = clean_customer_name(name)
        if not text:
            return None
        with self.connection_factory() as connection:
            return repository.find_by_name(connection, text)

    def create(self, name: object, *, actor: str) -> Customer:
        text = clean_customer_name(name)
        if not text:
            raise CustomerValidationError("customer.name_required", "客户名称不能为空。")
        with self.connection_factory() as connection:
            if repository.find_by_name(connection, text):
                raise CustomerValidationError("customer.duplicate", f"客户 {text} 已存在。")
            customer = repository.insert_customer(connection, name=text, sync_id=customer_sync_id(text))
            log_event(connection, "新增客户", "customer", customer.name, actor=actor)
            connection.commit()
            return customer

    def rename(self, customer_id: int, name: object, *, actor: str) -> Customer:
        text = clean_customer_name(name)
        if not text:
            raise CustomerValidationError("customer.name_required", "客户名称不能为空。")
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            existing = repository.find_by_name(connection, text)
            if existing and existing.id != customer.id:
                raise CustomerValidationError("customer.duplicate", f"客户 {text} 已存在。")
            old_name = customer.name
            repository.rename_customer(connection, customer.id, name=text)
            moved = 0
            if old_name != text:
                moved = repository.rename_quote_references(connection, old_name=old_name, new_name=text)
            detail = f"{old_name} → {text}"
            if moved:
                detail += f"，同步更新 {moved} 条报价记录"
            log_event(connection, "客户改名", "customer", text, detail, actor=actor)
            connection.commit()
            renamed = repository.get_customer(connection, customer.id)
            assert renamed is not None
            return renamed

    def delete(self, customer_id: int, *, actor: str) -> Customer:
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            references = repository.count_quote_references(connection, customer.name)
            if references:
                raise CustomerValidationError(
                    "customer.in_use",
                    f"客户 {customer.name} 有 {references} 条报价记录，不能删除；如需停用请先处理这些记录。",
                )
            repository.delete_customer(connection, customer.id)
            log_event(connection, "删除客户", "customer", customer.name, actor=actor)
            connection.commit()
            return customer
