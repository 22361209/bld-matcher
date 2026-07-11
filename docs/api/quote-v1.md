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

列表支持 `customer_name`、`bld_no`、`date_from`、`date_to`、`currency`、`quoted_by`、`limit` 和 `offset`。API v1 不接受或返回本机 `attachment_path`。

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
  "source_type": "wechat",
  "source_text": "Customer message",
  "on_behalf_of": "sales note"
}
```

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

`/api/quotes`、`/api/quotes/latest` 和 `/api/quotes/{id}` 继续保持旧响应格式，供已有消费者迁移。旧接口与网页、Excel 导入仍调用同一 `QuoteService`，但新集成不得继续扩展旧合同。
