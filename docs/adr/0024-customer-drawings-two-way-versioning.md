# ADR 0024: Customer Drawings With Two-Way Versioning And Quote Link Table

- Status: accepted
- Date: 2026-08-07
- Owners: BLD
- Partially superseded by: ADR 0025（商品行与双图纸位模型）及 ADR 0026（删除整个客户产品时清除图纸和报价图纸关联）

## Context

业务中存在两类长期图纸往来：客户发来的图纸（客户来图）和我方出具给客户的图纸（我方出图）。这些图纸按客户归属、多次改版，需要保留每个历史版本、版本代号（如 Rev B）和修改备注，与客户资料库（ADR 0021）按模板分类的语义不同。报价阶段需要把报价行与具体图纸版本关联，但该能力放在二期实现；一期必须先把关联表建好，避免二次迁移。

## Decision

1. 新增 `customer_drawing_groups` / `customer_drawing_files` 两张表，镜像客户资料库的数据模型：档案组保存客户、方向（`customer`=客户来图、`issued`=我方出图）、可选关联 BLD 号、标题、图号、当前版本与归档标记；文件表保存不可变版本记录，含版本号、版本代号、原始文件名、存储路径、SHA-256、上传人与备注。每个版本只允许一个文件（`UNIQUE(group_id, version_no)`）。`bld_no` 为自由文本、可空，允许先传图后建产品，不做产品存在性强校验。
2. 报价行与图纸版本的关联表 `quote_record_drawings` 随一期迁移（035）先行建设，只建表不写业务代码，供二期报价关联使用。
3. 图纸文件写入 `data/customer_files/<customer_sync_id>/drawings/<group_sync_id>/v0001/<uuid>-<安全文件名>`，与客户资料共用根目录但以 `drawings` 段隔离。写入沿用临时文件、流式 SHA-256 与大小校验、魔数校验、原子落位、失败补偿清空的管线；文件永不物理删除，归档只置 `archived` 标记，可取消归档。
4. 图纸格式限定 pdf/png/jpg/jpeg/webp，与客户资料库的在线预览能力对齐；大小上限沿用 `MAX_UPLOAD_MB`。下载与预览为只读 GET，响应带 `private, no-store` 与 `nosniff`。
5. 权限复用现有点，不新增：查看/预览/下载 `view_customers`；新增档案、编辑信息、上传新版本 `edit_customers`；归档与取消归档 `delete_customers`。
6. 客户详情页新增第五个页签"图纸"，按客户来图/我方出图分组展示档案卡片，新增、编辑、传新版本、归档均使用 `<details>` 折叠面板加普通表单 POST，与"模板与文件"页签同风格。一期不新增公共 API。

## Compatibility

既有客户资料库、报价、合同行为不变；新页签为增量能力。`quote_record_drawings` 暂无任何读写方，二期启用时通过报价公开服务写入，不得让报价模块直接访问图纸仓储。

## Quote Linking (Phase 2)

二期在报价域落地报价行与图纸版本的关联，关键点：

1. 关联粒度钉到版本：`quote_record_drawings.drawing_file_id` 指向 `customer_drawing_files` 的具体版本行，图纸档案后续上传新版本不影响已有关联；"已有新版"只在页面层比较 `current_version` 与关联版本的 `version_no` 给出提示，不做强制更新。
2. 表归属与跨模块边界：`quote_record_drawings` 由报价模块的仓储独占读写（关联、解除、按报价行查询、删除报价行时同事务级联）；图纸元数据（归属客户、方向、标题、当前版本）一律通过客户图纸模块的公开服务 `file_references` / `list_for_customer` 经 quotes 侧 `CustomerDrawingDirectoryPort` 适配器获取，报价模块不直接访问图纸仓储或图纸表。
3. 跨客户校验：关联时要求图纸档案的 `customer_id` 与报价行 `customer_id` 一致；历史报价行 `customer_id` 为 NULL 时按 `customer_name` 对 customers 表 NOCASE 匹配回退（复用 CustomerDirectory 端口的 find_id），无法解析或客户不一致即拒绝。
4. 幂等与级联：`UNIQUE(quote_record_id, drawing_file_id)` 加 `INSERT OR IGNORE` 保证重复关联不报错不重复；删除报价行时在删除 revisions 的同一事务里先删关联行。图纸文件永不物理删除，无反向级联；归档不影响已关联图纸的下载/预览（`allow_archived=True`）。
5. 权限复用现有点：关联/解除 `edit_customer_prices`，报价详情查看 `view_customer_prices`，图纸下载/预览入口按 `view_customers` 条件渲染，不新增权限点、不新增 REST API。

## Consequences

- 图纸与客户资料共用 `data/customer_files/` 根目录，NAS 备份范围不变。
- 每版本单文件简化了版本语义与报价关联粒度（关联精确到文件行），多页图纸需以 PDF 合页后上传。
- 客户来图与我方出图在同一客户下分方向管理，列表与统计按未归档口径计算。

## Verification

- 迁移 035 在新建与既有数据库上创建三张表及索引，主 schema 与迁移保持一致。
- 图纸 service/repository 测试覆盖创建、元信息更新、版本自增与并发占位、双向分组、归档/取消归档语义、路径穿越/格式/大小/魔数拒绝、数据库失败补偿清理。
- Web 适配器测试覆盖权限门控、下载/预览响应头与归档图纸的可下载性。
- `uv run python scripts/verify.py` 通过。
