# ADR 0025: Customer Products Rework — Dual Drawing Slots Per Customer Product

- Status: accepted
- Date: 2026-08-07
- Owners: BLD
- Partially superseded by: ADR 0026（新增时上传客户图纸、客户产品及其全部图纸删除生命周期）
- Supersedes: ADR 0024 中「按方向分组的图纸档案」模型与页签设计（报价行关联图纸版本的关联表、权限点与级联语义继续有效并适配新模型）

## Context

ADR 0024 落地的客户图纸按「客户来图 / 我方出图」方向分组、自由标题与可选 BLD 号关联，实际使用中不符合期望：业务真正管理的是"这个客户的这个商品（BLD 号）"及其双方图纸。自由建档导致同一商品出现多条档案、BLD 关联缺失、报价行关联入口难以定位。同时"当前版本恒等于最新版本、档案可归档"的语义与现场"回拨旧版本应付客户/产线"的习惯不符。

用户确认的决策：报价行关联图纸版本能力保留并适配新模型；客户商品行不提供删除；客户商品的 BLD 号必须在该客户报价历史中出现过；产品目录图纸保持独立，仅作为 BLD 图纸位的快捷引入来源。

## Decision

1. **以客户商品行为中心**：新增 `customer_products` 表（客户 + BLD 号 + 客户产品编码/名称，`UNIQUE(customer_id, bld_no COLLATE NOCASE)`）。每个商品行固定两个图纸位：`kind='bld'`（BLD 图纸）与 `kind='customer'`（客户图纸），重建后的 `customer_drawing_groups` 以 `UNIQUE(customer_product_id, kind)` 表达图纸位，懒创建于首次上传。旧 `direction`/`title`/`drawing_no`/`archived`/自由 `bld_no` 列废弃。
2. **版本指针可回拨**：`current_version` 不再恒等于最大版本号。上传新版本自动成为当前版本；任意历史版本可"设为当前版本"。版本与文件永不物理删除，`quote_record_drawings` 的既有版本级关联不受影响。
3. **BLD 必须命中报价历史**：创建客户商品时强校验 BLD 号出现在该客户报价历史中（`customer_id` 或 `customer_name` NOCASE 双轨回退），否则拒绝；客户产品编码/名称缺省时分别回填报价历史客户料号与产品目录品名。BLD 号建立后不可修改。
4. **目录图纸快捷引入**：产品目录图纸保持独立存储与生命周期；BLD 图纸位提供"引入产品目录图纸"，复制目录 PDF 为该图纸位的新版本。目录无图纸时前端禁用按钮，服务端同样拒绝。
5. **报价关联适配**：quotes 侧 `CustomerDrawingDirectoryAdapter` 改读商品行 + 双图纸位结构，`linkable_versions` 聚合该客户全部图纸位版本，方向文案改为"BLD 图纸 / 客户图纸"；`DrawingFileReference` 移除归档概念，关联不再检查归档守卫。跨客户校验、幂等关联、删除报价行级联不变。
6. **Web 层**：客户详情页"图纸"页签替换为"商品"页签（商品表格 + 新增/编辑弹窗 + 图纸预览/上传弹窗），`view=drawings` 归一到 overview。预览弹窗内左右箭头按版本循环切换、可回拨当前版本、拖放上传新版本；`versions.json` 只读端点（`view_customers`）供弹窗取数，写操作沿用表单/fetch POST（`edit_customers`）。下载/预览文件路由不变。

## Compatibility

- 迁移 036 演进 035：`customer_drawing_groups` 重建（rename → 建新表 → 按 `(customer_id, bld_no)` 归并回填 `customer_products` → drop 旧表），group `sync_id` 保留，磁盘路径 `data/customer_files/<customer>/drawings/<group_sync_id>/vNNNN/` 继续有效；`customer_drawing_files`、`quote_record_drawings` 不动。
- 既有报价行关联保持有效；权限点不新增（`view_customers` / `edit_customers`，报价关联仍 `edit_customer_prices`）。不新增公共 API。

## Consequences

- 一个客户同一 BLD 号只有一条商品行，图纸位固定两个，消除了重复档案与方向歧义。
- 未报过价的 BLD 无法建立客户商品，报价流程成为图纸管理的前置；历史无报价客户的图纸需先补报价。
- 回拨当前版本不产生新版本行，审计记录"设置当前图纸版本"事件；报价行已关联的旧版本仍可预览下载，页面层"已有新版"提示逻辑不变。
- 商品行不可删除；错误建立的行只能通过编辑编码/名称修正，BLD 错误需由后续决策补充处置手段。

## Verification

- 迁移 036 在新建与既有数据库上演进，主 schema 与迁移一致；旧方向分组数据归并为商品行与图纸位。
- service/repository 测试覆盖报价历史强校验、双图纸位懒创建、版本上传自动置当前、回拨任意版本、目录图纸引入与缺失拒绝。
- Web 测试覆盖路由权限门控、create/update/上传/设当前/引入的 service 调用与 JSON/重定向双形态、versions.json 结构，以及商品页签的列、徽标与权限差异渲染。
- `uv run python scripts/verify.py` 通过。
