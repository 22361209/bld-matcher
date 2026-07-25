# Quote API v1

报价 API 面向 OpenClaw、Hermes、WorkBuddy、MCP 和其他受控集成。机器合同以运行中的 `GET /api/v1/openapi.json` 为准，本文件解释调用顺序和并发规则。

## Authentication

所有请求使用：

```http
Authorization: Bearer <API Key>
```

读取需要 `quotes:read`，创建和修订需要 `quotes:write`。审计 actor 始终来自 API Key 对应的服务端 Principal。

## Resources

```text
GET   /api/v1/quotes
GET   /api/v1/quotes/latest?customer_name=...&bld_no=...
GET   /api/v1/quotes/{quote_id}
POST  /api/v1/quotes
PATCH /api/v1/quotes/{quote_id}
```

列表支持 `customer_name`、`bld_no`、`date_from`、`date_to`、`currency`、`quoted_by`、`quote_no`、`limit` 和 `offset`。API v1 不接受或返回本机 `attachment_path`。

每条报价记录都有服务端生成的 `quote_no`（报价单号，格式 `Q` + 6 位日期 + 3 位当日序号，如 `Q260725001`）。网页手工新增和 API 新增每条各生成一个单号；网页询价写入和 Excel 导入的整批记录共用一个单号。`quote_no` 由系统维护，创建和修订请求都不能指定或修改；历史记录在迁移 026 中按报价日期逐行补编号。列表按报价日期新→旧、报价单号新→旧、同一单号内按写入顺序返回。

报价人和来源由服务端按可信入口确定：网页手工新增记录为当前登录账号和 `manual`，Excel 导入为当前登录账号和 `excel`，API 新增为 API Key 对应的 Principal 和 `api`。为兼容旧调用方，创建请求仍可携带已废弃的 `quoted_by` 和 `source_type`，但服务端不会采用调用方提供的值；修订请求不再接受这两个系统字段。兼容性决定见 [ADR 0009](../adr/0009-server-owned-quote-attribution.md)。

## Create

创建必须使用调用方生成的稳定幂等键：

```http
POST /api/v1/quotes
Authorization: Bearer <API Key>
Idempotency-Key: workbuddy-quote-20260711-001
Content-Type: application/json
```

```json
{
  "customer_name": "Example Customer",
  "bld_no": "K6004LB",
  "customer_product_code": "CUSTOMER-001",
  "tax_price": 12.34,
  "net_price": 11.22,
  "currency": "USD",
  "quote_date": "2026-07-11",
  "remark": "Customer asked for discount",
  "on_behalf_of": "sales note"
}
```

备注使用 `remark` 字段（最长 5000 字符），创建和修订都可以写入。可写字段以运行中的 `GET /api/v1/openapi.json` 里 `QuoteCreateRequest` 的 schema 为准；请求模型是严格模式，携带 schema 未定义的字段名（例如 `note`、`comment`、`memo`）会被拒绝并返回 422，请使用准确的字段名。

相同 Principal、端点、方法和 `Idempotency-Key` 的相同请求会重放原响应，并返回 `Idempotency-Replayed: true`。同一个 Key 携带不同请求会返回 `409 idempotency.conflict`。

## Update

先读取报价并保存响应中的 `ETag`，再使用该版本修订：

```http
PATCH /api/v1/quotes/42
Authorization: Bearer <API Key>
Idempotency-Key: workbuddy-quote-update-42-001
If-Match: "3"
Content-Type: application/json
```

```json
{"tax_price": 12.5, "remark": "Customer confirmed"}
```

成功后版本递增并返回新的 ETag。若其他调用者已先修改，接口返回 `412 quote.version_conflict`，其中 `details.current_version` 表示最新版本；调用方必须重新读取后决定是否再次提交。

## Compatibility

旧 `/api/quotes`、`/api/quotes/latest` 和 `/api/quotes/{id}` 已移除（ADR 0013），报价对外 API 只有本文件描述的 v1 合同。历史 `price` 字段也已从请求和响应中删除，价格只使用 `tax_price` 和 `net_price`。
