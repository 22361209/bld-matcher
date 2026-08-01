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
    PermissionDefinition("manage_customers", "维护客户信息", "维护客户档案、联系人和客户资料文件。", "询价、客户与报价"),
    PermissionDefinition("view_customer_prices", "查看报价记录", "查看客户报价和价格历史。", "询价、客户与报价"),
    PermissionDefinition("manage_customer_prices", "维护报价记录", "新增、修正、删除报价并写入询价报价；同时控制产品目录单价的可见性。", "询价、客户与报价"),
    PermissionDefinition(
        "generate_purchase_contract",
        "合同管理",
        "查看合同记录并生成采购、销售合同。",
        "合同与生产",
    ),
    PermissionDefinition("generate_material_sheet", "生成生产料单", "查询并生成生产料单。", "合同与生产"),
    PermissionDefinition("manage_materials", "维护生产资料", "维护材料明细和管件资料。", "合同与生产"),
    PermissionDefinition("sync_product_data", "业务数据同步", "导入或导出跨设备业务数据包。", "数据管理"),
    PermissionDefinition(
        "recognize_shipments",
        "货物识别（保留）",
        "历史兼容权限，当前没有网页入口。",
        "保留能力（当前无网页入口）",
        active=False,
    ),
    PermissionDefinition(
        "generate_shipping_notice",
        "发货通知（保留）",
        "历史兼容权限，当前没有网页入口。",
        "保留能力（当前无网页入口）",
        active=False,
    ),
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
        "recognize_shipments",
        "generate_shipping_notice",
    },
    "user": {
        "generate_match",
        "generate_material_sheet",
        "recognize_shipments",
        "generate_shipping_notice",
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
