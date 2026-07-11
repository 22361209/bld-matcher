from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


API_SCOPE_LABELS = {
    "api:read": "API 元数据",
    "products:read": "读取产品",
    "inquiries:run": "运行询价",
    "artifacts:read": "读取结果文件",
    "quotes:read": "读取报价",
    "quotes:write": "写入报价",
    "contracts:generate": "生成合同",
    "jobs:read": "读取任务",
    "jobs:cancel": "取消任务",
}
ALL_API_SCOPES = frozenset(API_SCOPE_LABELS)
DEFAULT_API_SCOPES = frozenset(
    {
        "api:read",
        "products:read",
        "inquiries:run",
        "artifacts:read",
        "quotes:read",
        "jobs:read",
    }
)
# Keys created before scopes existed retain their historical capabilities.
LEGACY_COMPATIBILITY_SCOPES = ALL_API_SCOPES


@dataclass(frozen=True, slots=True)
class ApiPrincipal:
    key_id: int | None
    integration_name: str
    scopes: frozenset[str]
    expires_at: datetime | None = None

    @property
    def subject(self) -> str:
        if self.key_id is not None:
            return f"key:{self.key_id}"
        return f"integration:{self.integration_name}"

    def has_scopes(self, required: set[str] | frozenset[str]) -> bool:
        return required.issubset(self.scopes)
