from __future__ import annotations

from .domain import ProductOptionValue
from .option_values import (
    add_option_value,
    delete_option_value,
    get_option_value,
    list_option_values,
    option_value_exists,
    rename_option_value,
)
from .repository_context import ProductRepositoryContext


class ProductOptionRepositoryMixin(ProductRepositoryContext):
    def option_values(self) -> list[ProductOptionValue]:
        return list_option_values(self.connection)

    def get_option_value(self, option_id: int) -> ProductOptionValue | None:
        return get_option_value(self.connection, option_id)

    def option_value_exists(self, kind: str, value: str) -> bool:
        return option_value_exists(self.connection, kind, value)

    def add_option_value(self, kind: str, value: str, *, actor: str) -> bool:
        created = add_option_value(self.connection, kind, value)
        if created:
            self._log_event(
                self.connection,
                "新增产品候选值",
                "product_option_value",
                f"{kind}:{value}",
                "",
                actor=actor,
            )
        return created

    def rename_option_value(self, option_id: int, value: str, *, actor: str) -> ProductOptionValue | None:
        current = get_option_value(self.connection, option_id)
        if current is None:
            return None
        rename_option_value(self.connection, option_id, value)
        self._log_event(
            self.connection,
            "改名产品候选值",
            "product_option_value",
            f"{current.kind}:{value}",
            f"{current.value} -> {value}",
            actor=actor,
        )
        return get_option_value(self.connection, option_id)

    def delete_option_value(self, option_id: int, *, actor: str) -> ProductOptionValue | None:
        current = get_option_value(self.connection, option_id)
        if current is None:
            return None
        delete_option_value(self.connection, option_id)
        self._log_event(
            self.connection,
            "删除产品候选值",
            "product_option_value",
            f"{current.kind}:{current.value}",
            "",
            actor=actor,
        )
        return current
