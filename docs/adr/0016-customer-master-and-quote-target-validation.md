# ADR 0016: Customer Master Data And Quote Target Validation

- Status: accepted
- Date: 2026-07-28
- Owners: BLD

## Context

报价记录的客户名称和 BLD 号此前是自由文本：`build_quote_draft` 只校验非空，手动录入、Excel 导入和 API v1 都能写入任意拼写。长期结果是同一客户出现多个写法（如「宁波多迦」误作「浙江多迦」），按客户汇总和最新报价查询失真；目录里不存在的型号也能写入单价，后续按目录对账时成为孤儿数据。

产品候选值（ADR 0014）已验证「维护列表 + 录入下拉」的模式，但客户名涉及报价历史和跨设备同步，不能只做候选值：改名必须级联历史记录，同步必须解决两端名单不一致。

## Decision

1. 客户成为主数据：migration `027_customers` 建 `customers` 表（`name` 大小写不敏感唯一 + `sync_id`），初始名单从 `quote_records` 去重回填。`sync_id` 取规范化名称的确定性哈希（`stable_sync_id("customer", name.upper(), 1)`），任何设备上同名客户同 ID；改名保留 `sync_id`，同步按「更新名称」合并。
2. 报价目标硬校验集中在 `QuoteService`：`create / update / create_many / _apply_import_rows` 经新增 `ProductCatalogPort` / `CustomerDirectoryPort` 校验 BLD 号（`find_by_bld(active_only=False)`，停用产品仍可补历史价）和客户；不满足即拒绝（网页 flash / JSON，`QuoteValidationError`）。Excel 导入在 `preview_import` 阶段预检并把未知 BLD 行标记为 invalid。询价写入路径的 BLD 天然来自目录匹配，只新增客户存在性前置检查。
3. 客户维护走独立权限 `manage_customers`（初始仅 admin）：`/customers` 维护页支持新增/改名/删除；改名同事务级联 `quote_records.customer_name`，被报价引用的客户禁止删除。
4. 录入交互统一为 combobox：报价页表单、询价下载弹窗的 BLD 号（`/products/lookup`）和客户名（`/customers/lookup`）输入即过滤、方向键选择、回车填入；有维护权限时下拉尾部可「新增 "X"」快捷登记。产品候选值 picker 补齐同样的键盘交互。
5. 业务同步纳入 customers 数据集。导入报价包时 preview 列出本机未登记的客户，apply 要求逐一给出「新建」或「映射到已有客户」（`customer_mappings`），映射在落库前改写报价行客户名；未处理完整则拒绝导入。
6. API v1 新增 `GET /api/v1/customers?q=`（`quotes:read`，additive），供 agent 用客户简称匹配；agent 匹配到多个候选时必须向用户确认，无候选时引导先登记，不允许自行拼写全称。

## Compatibility

网页和 API 的写入路径行为收紧是有意的不兼容：此前能成功的「未知客户/未知 BLD」写入现在返回 400/422（`quote.customer_unknown` / `quote.bld_unknown`）。存量数据不受影响，只拦截新写入和修订。`GET /api/v1/customers` 为 additive 变更；OpenAPI 与路由快照同步更新。

## Consequences

- 客户名和 BLD 号有了唯一事实来源，报价可按客户/型号可靠汇总；「先报价、后补录目录」的旧习惯被阻断，需先登记。
- 迁移回填的名单可能继承历史错别字，维护页改名（级联历史记录）是修正通道。
- 同步两端代码需同版本：preview 对字段不一致的数据包已有「请先升级」拒绝逻辑。
- 客户创建不开放 API，agent 只能匹配不能登记，登记动作始终由人在维护页或录入下拉中完成。

## Verification

- `tests/test_customers_module.py`：名称规范化、重名拒绝、确定性 sync_id、改名级联、引用删除保护、模糊查询。
- `tests/test_quotes_module.py`：未知 BLD/客户在 create、update、create_many、导入应用、导入预览各路径的拒绝或跳过。
- `tests/test_business_sync.py`：preview 列出未知客户、无映射拒绝导入、新建与映射两种处理路径。
