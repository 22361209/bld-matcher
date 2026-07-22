# ADR 0013: Quote Price Field And Legacy Quote API Removal

- Status: accepted
- Date: 2026-07-22
- Owners: BLD

## Context

`quote_records.price` 是最早单价格列（migration 009）。migration 010 引入 `tax_price`/`net_price` 并把旧数据回填为 `tax_price = price` 之后，`price` 只作为镜像存在（创建时取 `tax_price`，缺省时退到 `net_price`），没有独立业务语义，但仍占据数据库 `NOT NULL` 列、领域模型、API v1 请求/响应 Schema（作为兼容别名）和旧 `/api/quotes` 响应。与此同时，旧 `/api/quotes` 系列接口的全部能力已被 `/api/v1/quotes` 覆盖（列表、创建、单条读取、修订、最新报价，外加幂等键和乐观并发），继续保留两套合同会让每一次报价合同演进都要维护双份适配。

## Decision

1. 彻底移除报价的 `price` 字段：`quote_records` 表通过 migration `024_drop_quote_record_price` 执行 `ALTER TABLE quote_records DROP COLUMN price`；新库 SCHEMA 不再创建该列；`QuoteRecord`、`QuoteDraft`、`storage_values()`、`payload()`（原 `legacy_payload()`）、API v1 的 `QuoteCreateRequest`/`QuotePatchRequest`/`QuoteResponse` 同步删除该字段。历史迁移 `010_quote_record_bld_prices` 的 `price` 回填增加列存在守卫，使其在无 `price` 列的新库上仍可重放。
2. 删除旧 `/api/quotes`、`/api/quotes/latest`、`/api/quotes/<id>` 四个路由及其辅助函数，报价对外 API 只保留 `/api/v1/quotes` 系列。`policy/legacy_allowlist.json` 和 `is_machine_api_path()` 移除对应条目。
3. 只传 `price` 的调用方（旧别名输入）不再被接受：v1 严格 Schema 返回 `422 request.invalid`；创建/修订必须提供 `tax_price` 或 `net_price` 至少一个。
4. `source_text` 和 `attachment_path` 仍按 ADR 0009 作为历史列保留，不在本次范围内。

## Compatibility

这是有意的破坏性变化：旧 `/api/quotes` 消费者必须迁移到 `/api/v1/quotes`（PUT 改为带 `If-Match` 的 PATCH，响应改为 `data.quote` 信封并携带 `ETag`）；任何读取响应中 `price` 或仅用 `price` 写入的集成必须改用 `tax_price`/`net_price`。OpenAPI 快照、路由快照和消费者合同测试随本决定一起更新。

## Consequences

- 报价价格只有含税/不含税两个业务字段，网页、Excel 导入、API v1 和服务层语义完全一致，不再维护镜像列。
- 报价对外 API 收敛为单一 v1 合同，后续演进不需要双份适配；`legacy_payload()` 更名为 `payload()`，仅用于修订日志和 v1 响应组装。
- 跨设备业务同步按本地 `PRAGMA table_info` 列清单写库：新版本接收旧版本数据包时自动忽略多出的 `price` 键。两侧都升级到本版本后再执行同步，避免旧版本接收缺少 `price` 键的新数据包。

## Verification

- 历史库迁移回归（含 `tests/fixtures/historical/v012-quotes-and-keys.sql` 矩阵）确认 `price` 列被移除且已有报价数据完好。
- API v1 合同测试确认只传 `price` 或税价双双缺失返回 `422 request.invalid`，OpenAPI 文档不再公开 `price`。
- 旧 `/api/quotes` 四个路由回归返回 404，路由快照不再包含这些条目。
- 网页新增/修正/删除报价、Excel 导入和跨设备同步全链路回归通过。
