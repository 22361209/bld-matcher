from __future__ import annotations

from .domain import ProductOptionValue
from .option_values import (
    add_option_value,
    delete_option_value,
    get_option_value,
    list_option_values,
    option_value_exists,
    rename_option_value,
    write_option_sort_orders,
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

    def move_option_value(self, option_id: int, direction: str, *, actor: str) -> ProductOptionValue | None:
        current = get_option_value(self.connection, option_id)
        if current is None or direction not in ("up", "down"):
            return None
        siblings = [option for option in self.option_values() if option.kind == current.kind]
        index = next((position for position, option in enumerate(siblings) if option.id == option_id), None)
        if index is None:
            return None
        neighbor = index - 1 if direction == "up" else index + 1
        if neighbor < 0 or neighbor >= len(siblings):
            return current
        siblings[index], siblings[neighbor] = siblings[neighbor], siblings[index]
        write_option_sort_orders(self.connection, current.kind, [option.id for option in siblings])
        self._log_event(
            self.connection,
            "调整产品候选值顺序",
            "product_option_value",
            f"{current.kind}:{current.value}",
            f"{'上移' if direction == 'up' else '下移'}到第 {neighbor + 1} 位",
            actor=actor,
        )
        return get_option_value(self.connection, option_id)
