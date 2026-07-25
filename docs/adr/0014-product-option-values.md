# ADR 0014: Product Option Values Controlled Vocabulary

- Status: accepted
- Date: 2026-07-25
- Owners: BLD

## Context

产品候选值（品牌/产品名称/产品状态）此前没有独立数据源：`GET /products/options`（新增/编辑产品表单的下拉候选）和目录导入模板的下拉与校验（`catalog_import_choices()`）都来自 `filter_options()` 对 `products` 全库去重。这意味着候选集合随产品数据隐式漂移，无法新增“还没有产品使用”的候选，也无法下架不再想要的候选；导入校验的“受控词表”实际上并不可控。

## Decision

1. 新增受控词表单表 `product_option_values(kind, value)`（`UNIQUE(kind, value)`，kind ∈ `brand`/`item`/`product_status`），作为候选值的唯一来源。新库 SCHEMA 直接建表；历史库由 migration `025_create_product_option_values` 建表并从 `products` 播种：品牌按 `canonicalize_brands` 换行拆分取并集，产品名称去空白非空去重，产品状态取 `canonical_product_status` 非空去重，全部 `INSERT OR IGNORE`，迁移幂等。
2. 写入自动登记：产品写入口 `upsert_product()` 成功 upsert 后，在同一事务内把本次的品牌（规范化后逐行）、产品名称、产品状态（canonical）`INSERT OR IGNORE` 进词表，保存/复制/导入全部经过该入口；跨设备数据同步（`SQLiteProductSyncRepository.apply()`）绕过 `upsert_product` 直写 `main.products`，在 apply 的同一事务内逐行单独登记，覆盖一致。
3. 数据源收口：`/products/options` 与 `catalog_import_choices()` 改为读词表（产品状态按 `format_product_status(value, "zh", multiline=False)` 输出），响应结构与 `CatalogImportChoices` 不变。`filter_options()` 与列头筛选面板保持按产品实际数据去重，不在本次范围。
4. 管理页 `/product-options`（权限 `manage_product_options`，仅 admin）提供三个分区的纯 CRUD：新增、改名、删除。删除/改名**不检查使用计数、不级联改产品数据**——删除只影响未来可选，产品行原值保留、显示不受影响。写操作遵循权限 + CSRF + 事务 + 审计四件套。

## Alternatives

- 继续用 `filter_options()` 全库去重并加“隐藏列表”：候选仍随产品数据漂移，导入校验依旧不可控，否决。
- 删除/改名时级联更新产品行：写放大且会改写历史数据原值，与“显示按存储原值渲染”的原则冲突，否决。

## Consequences

- 候选值可独立于产品数据维护：可以先登记候选再导入/录入，也可以删除不再使用的候选而不碰产品数据。
- 改名词表不会改产品行，可能出现产品原值不在词表中的情况；该产品下次保存时会把原值重新登记回词表（写入自动登记），属预期行为。
- 迁移 025 一次性播种历史数据；之后词表随产品写入自动增长，由管理页收敛。
- 管理员可以把某一类词表整体删空；目录导入校验对空词表整体跳过（`_validate_controlled_values` 的既有行为），此时该类退化为不校验，属已知边界，依赖管理员自律。
- 回滚：删除迁移记录与新表、还原 `/products/options` 与 `catalog_import_choices()` 的数据源即可，产品数据不受影响。

## Verification

- 历史库 fixture matrix 迁移回归覆盖 025；新库 SCHEMA 直接建表。
- 测试确认：播种幂等且包含多品牌/状态拆分结果；`POST /products/save` 后新值自动登记且不重复；管理页 CRUD、权限拦截与审计记录；`/products/options` 只返回词表值；词表新增 SERIES/ITEM 后目录导入被接受、词表外值仍被拒绝。
