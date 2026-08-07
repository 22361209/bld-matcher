from __future__ import annotations

from dataclasses import dataclass


ADMIN_ROLE_KEY = "admin"


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    key: str
    label: str
    description: str
    group: str
    assignable: bool = True
    active: bool = True


PERMISSION_DEFINITIONS: tuple[PermissionDefinition, ...] = (
    PermissionDefinition(
        "manage_users",
        "账号与角色管理",
        "新增、停用和编辑账号，并维护角色权限。此权限固定由管理员持有。",
        "系统与审计",
        assignable=False,
    ),
    PermissionDefinition("view_logs", "查看操作日志", "查看操作日志和系统更新记录。", "系统与审计"),
    PermissionDefinition("import_catalog", "导入产品目录", "下载目录模板并预览、确认目录导入。", "产品目录"),
    PermissionDefinition("export_catalog", "导出产品目录", "按当前筛选条件导出完整产品目录。", "产品目录"),
    PermissionDefinition("edit_products", "维护产品资料", "新增、编辑、停用产品及维护图片和图纸。", "产品目录"),
    PermissionDefinition("rename_products", "迁移 BLD NO.", "迁移产品 BLD NO. 及其关联业务记录。", "产品目录"),
    PermissionDefinition("manage_product_options", "维护基础选项", "维护品牌、产品名称和产品状态候选值。", "产品目录"),
    PermissionDefinition("manage_aliases", "维护号码映射", "维护人工号码映射和未命中号码归档。", "产品目录"),
    PermissionDefinition("generate_match", "处理询价", "运行号码或 Excel 匹配并下载匹配结果。", "询价、客户与报价"),
    PermissionDefinition("view_customers", "查看客户信息", "查看客户档案、联系人和客户资料文件。", "询价、客户与报价"),
    PermissionDefinition("add_customers", "新增客户", "新增客户档案。", "询价、客户与报价"),
    PermissionDefinition("edit_customers", "修正客户", "修改客户档案、联系人和客户资料文件，启用或停用客户。", "询价、客户与报价"),
    PermissionDefinition(
        "delete_customers",
        "删除客户",
        "删除客户档案、联系人、客户产品及其图纸，归档客户资料文件。",
        "询价、客户与报价",
    ),
    PermissionDefinition("view_customer_prices", "查看报价记录", "查看客户报价记录。", "询价、客户与报价"),
    PermissionDefinition("view_price_history", "查看历史价格", "查看客户与产品的历史报价价格。", "询价、客户与报价"),
    PermissionDefinition("add_customer_prices", "新增报价", "新增、导入报价并从询价结果写入报价；同时参与控制产品目录单价的可见性。", "询价、客户与报价"),
    PermissionDefinition("edit_customer_prices", "修正报价", "修正已有报价并调整询价结果报价；同时参与控制产品目录单价的可见性。", "询价、客户与报价"),
    PermissionDefinition("delete_customer_prices", "删除报价", "删除报价记录；同时参与控制产品目录单价的可见性。", "询价、客户与报价"),
    PermissionDefinition("view_contracts", "查看合同", "查看采购、销售合同记录并下载合同文档。", "合同与生产"),
    PermissionDefinition("generate_contract", "生成合同", "生成采购、销售合同。", "合同与生产"),
    PermissionDefinition("generate_material_sheet", "生成生产料单", "查询并生成生产料单。", "合同与生产"),
    PermissionDefinition("manage_material_items", "维护材料明细", "新增、编辑、停用材料明细，导入材料数据并上传物料图纸。", "合同与生产"),
    PermissionDefinition("manage_tube_items", "维护管件资料", "新增、编辑管件资料。", "合同与生产"),
    PermissionDefinition("sync_product_data", "业务数据同步", "导入或导出跨设备业务数据包。", "数据管理"),
)

PERMISSION_BY_KEY = {definition.key: definition for definition in PERMISSION_DEFINITIONS}
ALL_PERMISSION_KEYS = frozenset(PERMISSION_BY_KEY)
ASSIGNABLE_PERMISSION_KEYS = frozenset(
    definition.key for definition in PERMISSION_DEFINITIONS if definition.assignable
)
ADMIN_ONLY_PERMISSION_KEYS = ALL_PERMISSION_KEYS - ASSIGNABLE_PERMISSION_KEYS

LEGACY_ROLE_LABELS = {
    ADMIN_ROLE_KEY: "管理员",
    "editor": "编辑员",
    "user": "普通用户",
    "viewer": "只读用户",
}

LEGACY_ROLE_DESCRIPTIONS = {
    ADMIN_ROLE_KEY: "系统固定角色，始终拥有全部权限。",
    "editor": "维护产品和号码映射，并处理询价与生产料单。",
    "user": "处理询价并生成生产料单。",
    "viewer": "仅查看无需额外授权的业务资料。",
}

LEGACY_ROLE_PERMISSIONS = {
    ADMIN_ROLE_KEY: set(ALL_PERMISSION_KEYS),
    "editor": {
        "edit_products",
        "manage_aliases",
        "generate_match",
        "view_logs",
        "generate_material_sheet",
    },
    "user": {
        "generate_match",
        "generate_material_sheet",
    },
    "viewer": set(),
}


def permission_groups() -> tuple[tuple[str, tuple[PermissionDefinition, ...]], ...]:
    grouped: dict[str, list[PermissionDefinition]] = {}
    for definition in PERMISSION_DEFINITIONS:
        grouped.setdefault(definition.group, []).append(definition)
    return tuple((group, tuple(definitions)) for group, definitions in grouped.items())


def effective_permissions(
    role_key: str,
    role_permissions: set[str] | frozenset[str],
    overrides: dict[str, str] | None = None,
) -> frozenset[str]:
    if role_key == ADMIN_ROLE_KEY:
        return ALL_PERMISSION_KEYS
    overrides = overrides or {}
    allowed = {
        permission
        for permission in role_permissions
        if permission in ASSIGNABLE_PERMISSION_KEYS
    }
    allowed.update(
        permission
        for permission, effect in overrides.items()
        if effect == "allow" and permission in ASSIGNABLE_PERMISSION_KEYS
    )
    allowed.difference_update(
        permission
        for permission, effect in overrides.items()
        if effect == "deny"
    )
    return frozenset(allowed)
