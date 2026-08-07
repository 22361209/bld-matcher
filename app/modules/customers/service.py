from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any

from app.platform.audit_store import log_event
from app.platform.sync_identity import stable_sync_id

from . import repository
from .domain import (
    Customer,
    CustomerContact,
    CustomerQuoteSummary,
    CustomerSummary,
    CustomerValidationError,
    clean_customer_name,
    clean_customer_status,
    clean_single_line,
)


ConnectionFactory = Callable[[], AbstractContextManager[Any]]
OwnerValidator = Callable[[str], bool]


class CustomerBusinessReader:
    def quote_stats(self, customers: list[tuple[int, str]]) -> dict[int, dict[str, object]]:
        return {}

    def quote_history(self, customer_id: int, customer_name: str, *, limit: int = 50) -> list[CustomerQuoteSummary]:
        return []

    def rename_customer_references(self, customer_id: int, old_name: str, new_name: str) -> int:
        return 0



class ContractCustomerReader:
    def documents(self, customer_id: int, customer_name: str, *, limit: int = 50) -> list[dict[str, object]]:
        return []


def customer_sync_id(name: str) -> str:
    # 同一规范化名称在任何设备上生成相同 sync_id，业务同步据此识别同一客户；
    # 改名不改 sync_id，同步时按“更新名称”合并。
    return stable_sync_id("customer", name.upper(), 1)


def _bounded_filter_text(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class CustomerService:
    def __init__(
        self,
        connection_factory: ConnectionFactory,
        business_reader: CustomerBusinessReader | None = None,
        owner_validator: OwnerValidator | None = None,
        contract_reader: ContractCustomerReader | None = None,
    ) -> None:
        self.connection_factory = connection_factory
        self.business_reader = business_reader or CustomerBusinessReader()
        self.owner_validator = owner_validator
        self.contract_reader = contract_reader or ContractCustomerReader()

    def list(self) -> list[Customer]:
        with self.connection_factory() as connection:
            return repository.list_customers(connection)

    def list_summaries(
        self,
        *,
        query: object = "",
        status: object = "",
        owner_usernames: Sequence[object] = (),
    ) -> list[CustomerSummary]:
        normalized_query = _bounded_filter_text(query, limit=200)
        normalized_status = _bounded_filter_text(status, limit=20).lower()
        if normalized_status not in {"", "active", "inactive"}:
            normalized_status = ""
        normalized_owner_usernames = tuple(
            dict.fromkeys(
                username
                for value in owner_usernames
                if (username := _bounded_filter_text(value, limit=120))
            )
        )
        with self.connection_factory() as connection:
            customers = repository.list_customers(
                connection,
                query=normalized_query,
                status=normalized_status,
                owner_usernames=normalized_owner_usernames,
            )
            contacts = repository.primary_contacts(connection, [customer.id for customer in customers])
        stats = self.business_reader.quote_stats([(customer.id, customer.name) for customer in customers])
        return [
            CustomerSummary(
                customer=customer,
                primary_contact=contacts.get(customer.id),
                quote_count=int(stats.get(customer.id, {}).get("quote_count", 0)),
                quoted_product_count=int(stats.get(customer.id, {}).get("product_count", 0)),
                latest_quote_date=str(stats.get(customer.id, {}).get("latest_quote_date", "")),
                file_count=0,
            )
            for customer in customers
        ]

    def lookup(self, query: object, *, limit: int = 20) -> list[Customer]:
        text = clean_customer_name(query)
        with self.connection_factory() as connection:
            if not text:
                return repository.list_customers(connection, status="active")[:limit]
            return repository.lookup_customers(connection, text, limit=limit)

    def find_by_name(self, name: object) -> Customer | None:
        text = clean_customer_name(name)
        if not text:
            return None
        with self.connection_factory() as connection:
            return repository.find_by_name(connection, text, active_only=True)

    def get(self, customer_id: int) -> Customer:
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
        if customer is None:
            raise CustomerValidationError("customer.not_found", "客户不存在。")
        return customer

    def detail(self, customer_id: int, *, include_business: bool = True) -> dict[str, object]:
        customer = self.get(customer_id)
        with self.connection_factory() as connection:
            contacts = repository.list_contacts(connection, customer_id)
        stats = self.business_reader.quote_stats([(customer.id, customer.name)]).get(customer.id, {})
        summary = CustomerSummary(
            customer=customer,
            primary_contact=next((contact for contact in contacts if contact.is_primary), None),
            quote_count=int(stats.get("quote_count", 0)),
            quoted_product_count=int(stats.get("product_count", 0)),
            latest_quote_date=str(stats.get("latest_quote_date", "")),
            file_count=0,
        )
        return {
            "customer": customer,
            "summary": summary,
            "contacts": contacts,
            "quotes": self.business_reader.quote_history(customer.id, customer.name) if include_business else [],
            "contracts": self.contract_reader.documents(customer.id, customer.name) if include_business else [],
        }

    def create(self, name: object, *, actor: str) -> Customer:
        text = clean_customer_name(name)
        if not text:
            raise CustomerValidationError("customer.name_required", "客户名称不能为空。")
        if len(text) > 200:
            raise CustomerValidationError("customer.name_too_long", "客户名称不能超过 200 个字符。")
        with self.connection_factory() as connection:
            if repository.find_by_name(connection, text):
                raise CustomerValidationError("customer.duplicate", f"客户 {text} 已存在。")
            customer = repository.insert_customer(connection, name=text, sync_id=customer_sync_id(text))
            log_event(connection, "新增客户", "customer", customer.name, actor=actor)
            connection.commit()
            return customer

    def update_owner(self, customer_id: int, owner_username: object, *, actor: str) -> Customer:
        normalized_owner = clean_single_line(owner_username, limit=120)
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            if (
                normalized_owner != customer.owner_username
                and normalized_owner
                and self.owner_validator
                and not self.owner_validator(normalized_owner)
            ):
                raise CustomerValidationError("customer.owner_invalid", "负责人账号不存在或已停用。")
            if normalized_owner != customer.owner_username:
                repository.update_customer_owner(
                    connection,
                    customer.id,
                    owner_username=normalized_owner,
                )
                log_event(
                    connection,
                    "更新客户负责人",
                    "customer",
                    customer.name,
                    f"负责人：{customer.owner_username or '未指定'} → {normalized_owner or '未指定'}",
                    actor=actor,
                )
                connection.commit()
            updated = repository.get_customer(connection, customer.id)
            assert updated is not None
        return updated

    def rename(self, customer_id: int, name: object, *, reason: object, actor: str) -> Customer:
        normalized_name = clean_customer_name(name)
        normalized_reason = self._change_reason(reason)
        if not normalized_name:
            raise CustomerValidationError("customer.name_required", "客户名称不能为空。")
        if len(normalized_name) > 200:
            raise CustomerValidationError("customer.name_too_long", "客户名称不能超过 200 个字符。")
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            if normalized_name == customer.name:
                raise CustomerValidationError("customer.identity_unchanged", "客户名称未发生变化。")
            existing = repository.find_by_name(connection, normalized_name)
            if existing and existing.id != customer.id:
                raise CustomerValidationError("customer.duplicate", f"客户 {normalized_name} 已存在。")
            repository.update_customer_name(connection, customer.id, name=normalized_name)
            log_event(
                connection,
                "变更客户名称",
                "customer",
                normalized_name,
                f"名称：{customer.name} → {normalized_name}；原因：{normalized_reason}",
                actor=actor,
            )
            connection.commit()
            updated = repository.get_customer(connection, customer.id)
            assert updated is not None
        try:
            self.business_reader.rename_customer_references(customer.id, customer.name, normalized_name)
        except Exception:
            with self.connection_factory() as connection:
                repository.update_customer_name(connection, customer.id, name=customer.name)
                log_event(
                    connection,
                    "客户名称变更回滚",
                    "customer",
                    customer.name,
                    f"历史报价名称同步失败，客户名称已恢复；原变更原因：{normalized_reason}",
                    actor=actor,
                )
                connection.commit()
            raise
        return updated

    def update_code(self, customer_id: int, code: object, *, reason: object, actor: str) -> Customer:
        normalized_code = clean_single_line(code, limit=80)
        normalized_reason = self._change_reason(reason)
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            if normalized_code == customer.code:
                raise CustomerValidationError("customer.identity_unchanged", "客户编号未发生变化。")
            if normalized_code:
                same_code = repository.find_by_code(connection, normalized_code)
                if same_code and same_code.id != customer.id:
                    raise CustomerValidationError("customer.code_duplicate", f"客户编号 {normalized_code} 已被使用。")
            repository.update_customer_code(connection, customer.id, code=normalized_code)
            log_event(
                connection,
                "变更客户编号",
                "customer",
                customer.name,
                f"客户编号：{customer.code or '未填写'} → {normalized_code or '未填写'}；原因：{normalized_reason}",
                actor=actor,
            )
            connection.commit()
            updated = repository.get_customer(connection, customer.id)
            assert updated is not None
            return updated

    @staticmethod
    def _change_reason(value: object) -> str:
        reason = clean_single_line(value, limit=500)
        if not reason:
            raise CustomerValidationError("customer.change_reason_required", "请填写变更原因。")
        return reason

    def set_status(self, customer_id: int, status: object, *, actor: str) -> Customer:
        normalized = clean_customer_status(status)
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            if customer.status != normalized:
                repository.set_customer_status(connection, customer.id, status=normalized)
                action = "启用客户" if normalized == "active" else "停用客户"
                log_event(connection, action, "customer", customer.name, actor=actor)
                connection.commit()
            updated = repository.get_customer(connection, customer.id)
            assert updated is not None
            return updated

    def save_contact(
        self,
        customer_id: int,
        data: Mapping[str, object],
        *,
        actor: str,
        contact_id: int | None = None,
    ) -> CustomerContact:
        name = clean_single_line(data.get("name"), limit=120)
        if not name:
            raise CustomerValidationError("customer.contact_name_required", "联系人姓名不能为空。")
        title = clean_single_line(data.get("title"), limit=120)
        role = clean_single_line(data.get("role"), limit=120)
        phone = clean_single_line(data.get("phone"), limit=80)
        email = clean_single_line(data.get("email"), limit=200)
        wechat = clean_single_line(data.get("wechat"), limit=120)
        if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
            raise CustomerValidationError("customer.contact_email_invalid", "联系人邮箱格式无效。")
        is_primary = str(data.get("is_primary") or "").lower() in {"1", "true", "on", "yes"}
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            contacts = repository.list_contacts(connection, customer_id)
            existing = repository.get_contact(connection, customer_id, contact_id) if contact_id else None
            if contact_id and existing is None:
                raise CustomerValidationError("customer.contact_not_found", "联系人不存在。")
            if contact_id is None and not contacts:
                is_primary = True
            preferred_primary_id: int | None = None
            if existing is not None and existing.is_primary and not is_primary:
                alternative_ids = [contact.id for contact in contacts if contact.id != existing.id]
                preferred_primary_id = min(alternative_ids) if alternative_ids else existing.id
            if contact_id is None:
                contact = repository.insert_contact(
                    connection,
                    customer_id,
                    name=name,
                    title=title,
                    role=role,
                    phone=phone,
                    email=email,
                    wechat=wechat,
                    is_primary=is_primary,
                )
                action = "新增客户联系人"
            else:
                contact = repository.update_contact(
                    connection,
                    customer_id,
                    contact_id,
                    name=name,
                    title=title,
                    role=role,
                    phone=phone,
                    email=email,
                    wechat=wechat,
                    is_primary=is_primary,
                )
                assert contact is not None
                action = "更新客户联系人"
            if is_primary:
                preferred_primary_id = contact.id
            repository.normalize_primary_contact(
                connection,
                customer_id,
                preferred_contact_id=preferred_primary_id,
            )
            normalized_contact = repository.get_contact(connection, customer_id, contact.id)
            assert normalized_contact is not None
            log_event(connection, action, "customer_contact", name, f"客户：{customer.name}", actor=actor)
            connection.commit()
            return normalized_contact

    def delete_contact(self, customer_id: int, contact_id: int, *, actor: str) -> CustomerContact:
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            contact = repository.get_contact(connection, customer_id, contact_id)
            if contact is None:
                raise CustomerValidationError("customer.contact_not_found", "联系人不存在。")
            repository.delete_contact(connection, customer_id, contact_id)
            repository.normalize_primary_contact(connection, customer_id)
            log_event(
                connection,
                "删除客户联系人",
                "customer_contact",
                contact.name,
                f"客户：{customer.name}",
                actor=actor,
            )
            connection.commit()
            return contact

    def delete(self, customer_id: int, *, actor: str) -> Customer:
        with self.connection_factory() as connection:
            customer = repository.get_customer(connection, customer_id)
            if customer is None:
                raise CustomerValidationError("customer.not_found", "客户不存在。")
            raise CustomerValidationError(
                "customer.delete_disabled",
                f"客户 {customer.name} 不能删除；请改为停用，客户档案和全部历史记录将继续保留。",
            )
