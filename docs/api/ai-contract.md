# AI And API Contract

## Boundary

SQLite 是内部实现，不是集成接口。OpenClaw、Hermes、WorkBuddy、MCP、脚本和未来 AI 应用只能通过认证 API 调用 Application Service。

当前 `/api/internal/*` 和 `/api/quotes` 是兼容接口。新增能力进入 `/api/v1`，旧接口在消费者迁移完成前作为适配器保留。

当前兼容 Key 已执行安全基线：完整 Key 只在创建响应显示一次，响应禁止缓存；数据库只使用哈希校验并保留遮罩后缀，历史明文字段由迁移清空并删除。Key 已包含 Scopes 和可选到期时间，认证后生成不可伪造的 `ApiPrincipal`，审计身份取服务端 Key 名称，不接受客户端自报 actor。

`GET /api/v1` 返回当前能力，`GET /api/v1/openapi.json` 提供 OpenAPI 3.1 唯一机器合同。阶段 2 已落地 Principal、Scope、请求 ID、稳定错误、Pydantic Schema、OpenAPI 注册和 SQLite 幂等存储；报价资源已经开放，调用说明见 `docs/api/quote-v1.md`，其他领域按后续阶段逐项开放。

## Resource Model

```text
GET  /api/v1/products/search
POST /api/v1/inquiries/analyze
POST /api/v1/inquiries/export
GET  /api/v1/quotes
POST /api/v1/quotes
PATCH /api/v1/quotes/{id}
GET  /api/v1/jobs/{id}
POST /api/v1/jobs/{id}/cancel
GET  /api/v1/artifacts/{id}
```

长任务返回 `202 Accepted`、`job_id` 和状态 URL。文件返回 artifact ID、文件名、大小、校验值、过期时间和授权下载 URL，不返回绝对路径。

## Schemas And Errors

请求和响应使用 Pydantic 2 模型，OpenAPI 是机器可读唯一事实来源。每个操作的 `x-required-scopes` 必须与路由装饰器一致。成功响应包含：

```json
{"api_version":"1","request_id":"...","data":{},"warnings":[]}
```

错误响应包含稳定字段：

```json
{"api_version":"1","request_id":"...","error":{"code":"quote.invalid_price","message":"...","details":{},"retryable":false}}
```

`code` 是消费者逻辑依据，`message` 只供人阅读。不得把 Python 异常文本直接作为合同。

## Authentication And Scopes

API Key 创建时只显示一次，数据库保存哈希、名称、后缀、Scopes、创建者、到期时间和最近使用时间。认证后生成强类型 `ApiPrincipal`，包含 `key_id`、`integration_name`、`scopes` 和到期信息。历史 Key 在迁移时获得兼容 Scope；新 Key 默认只读，写 Scope 必须由管理员明确选择。

建议 Scopes：

- `products:read`
- `inquiries:run`
- `artifacts:read`
- `quotes:read`
- `quotes:write`
- `contracts:generate`
- `jobs:cancel`

审计 actor 来自 Principal。客户端可提交 `on_behalf_of` 作为非可信业务说明，但不能覆盖真实调用者。

## Mutation Safety

- 创建和导出支持 `Idempotency-Key`，重复请求返回原结果。
- 修订使用版本号或 `If-Match`，避免覆盖并发修改。
- 财务、停用、覆盖和批量操作支持 `dry_run`；高风险动作需要短期确认令牌。
- 所有修订保存 before/after、Principal、request ID、原因和时间。
- Key 默认只读，写 Scope 单独授予和轮换。
- 幂等记录默认保留 24 小时；相同 Principal、端点、方法和 Key 的同一请求重放原响应，不同请求返回稳定冲突错误。

## AI Provider Egress

- Provider、base URL、模型和密钥只由管理员配置或部署环境提供。
- 普通请求不得覆盖网络目标、代理、密钥或模型白名单。
- 每次外发记录数据类型、供应商、模型、调用者、Token、费用、耗时和结果状态。
- 上传内容和 OCR 文本一律视为不可信数据，不得作为系统指令执行。
- 新供应商接入需要 ADR，说明数据区域、保留策略、密钥管理、失败策略和退出方案。

## Consumers

REST/OpenAPI 是规范接口。CLI 和 MCP 是薄适配器，必须调用同一 API 或 Application Service，不能复制匹配逻辑和数据库访问。消费者合同测试覆盖 OpenClaw、Hermes、WorkBuddy 使用的字段和错误码。
