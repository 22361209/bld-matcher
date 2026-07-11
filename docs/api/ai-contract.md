# AI And API Contract

## Boundary

SQLite 是内部实现，不是集成接口。OpenClaw、Hermes、WorkBuddy、MCP、脚本和未来 AI 应用只能通过认证 API 调用 Application Service。

当前 `/api/internal/*` 和 `/api/quotes` 是兼容接口。新增能力进入 `/api/v1`，旧接口在消费者迁移完成前作为适配器保留。

当前兼容 Key 已执行安全基线：完整 Key 只在创建响应显示一次，响应禁止缓存；数据库只使用哈希校验并保留遮罩后缀，历史明文字段由迁移清空并删除。Key 已包含 Scopes 和可选到期时间，认证后生成不可伪造的 `ApiPrincipal`，审计身份取服务端 Key 名称，不接受客户端自报 actor。

`GET /api/v1` 返回当前能力，`GET /api/v1/openapi.json` 提供 OpenAPI 3.1 唯一机器合同。Principal、Scope、请求 ID、稳定错误、Pydantic Schema、OpenAPI 注册、SQLite 幂等、报价、产品、询价、artifact 和持久 jobs 资源均已落地。调用说明见 `docs/api/quote-v1.md` 和 `docs/api/product-inquiry-v1.md`。

`contracts/openapi-v1.json` 是提交快照，统一验收会从隔离数据库重新生成并精确比较。OpenAPI 变更必须先做兼容判断，再显式更新快照。旧 OpenClaw 与 v1 的核心匹配字段由同一行为测试覆盖。

## Resource Model

```text
GET  /api/v1/products/search
POST /api/v1/inquiries/analyze
POST /api/v1/inquiries/export
GET  /api/v1/quotes
POST /api/v1/quotes
PATCH /api/v1/quotes/{id}
GET  /api/v1/jobs/{id}
GET  /api/v1/jobs/{id}/result
POST /api/v1/jobs/{id}/cancel
GET  /api/v1/artifacts/{id}
```

长任务返回 `202 Accepted`、`job_id` 和状态 URL。状态读取需要 `jobs:read`；取消需要 `jobs:cancel` 和 `Idempotency-Key`。排队、运行、完成、失败和取消使用稳定状态；结果未完成时 `/result` 返回 `job.not_ready`。任务请求载荷、租约和内部路径不进入公开响应。

文件返回 artifact ID、文件名、大小、校验值、过期时间和授权下载 URL，不返回绝对路径。artifact 默认保留 24 小时并绑定创建它的 `ApiPrincipal.subject`。下载不存在、过期或其他 Principal 的 artifact 统一返回 404；服务端路径只保存在内部元数据表。统一保留期执行器会清理过期元数据与文件，并保护仍有效的 artifact。

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
- `jobs:read`
- `jobs:cancel`

审计 actor 来自 Principal。客户端可提交 `on_behalf_of` 作为非可信业务说明，但不能覆盖真实调用者。

## Mutation Safety

- 创建和导出支持 `Idempotency-Key`，重复请求返回原结果。
- 修订使用版本号或 `If-Match`，避免覆盖并发修改。
- 财务、停用、覆盖和批量操作支持 `dry_run`；高风险动作需要短期确认令牌。
- 所有修订保存 before/after、Principal、request ID、原因和时间。
- Key 默认只读，写 Scope 单独授予。管理页按 `BLD_API_KEY_ROTATION_DAYS` 提醒轮换，但不会自动停用；应先验证新 Key 再停用旧 Key。
- 幂等记录默认保留 24 小时；相同 Principal、端点、方法和 Key 的同一请求重放原响应，不同请求返回稳定冲突错误。

## AI Provider Egress

- Web 运行时的 Provider、base URL、模型、密钥、代理和 allowlist 只由部署环境提供；命令行人工工具的显式参数不进入 Web 请求边界。
- 普通请求不得覆盖网络目标、代理、密钥或模型白名单。
- 目标必须匹配精确 host 白名单并使用 HTTPS；HTTP 只允许 loopback，重定向必须保持配置的 origin 且不得携带账号、查询参数或片段，环境代理默认关闭。
- Provider 统一设置超时、2 MiB 响应上限、有限指数退避、进程内并发上限和稳定错误码；每次尝试之间执行取消/停机检查并续租。
- 每次逻辑外发在 `ai_provider_calls` 记录任务、数据类型、供应商、模型、调用者、尝试次数、Token、估算费用、耗时和结果状态；停机/取消中断使用 `interrupted` 状态，不保存密钥和图片正文。
- 上传内容和 OCR 文本一律视为不可信数据，不得作为系统指令执行。
- 新供应商接入需要 ADR，说明数据区域、保留策略、密钥管理、失败策略和退出方案。

## Runtime Ownership

Web 只提交任务；独立 Worker 调用同一 Application Service 和 AI Provider。Worker 使用持久租约和限频心跳，进程异常后由过期租约恢复；正常停机在受控检查点重新排队且不消耗 attempt。任务取消在 Handler 最终提交点前生效，越过提交点后完成优先。`/health/ready` 默认要求新鲜 Worker 心跳，部署还必须执行 `scripts/runtime_probe.py`。上传、输出、任务、幂等记录、AI 调用、心跳和备份的保留期与操作方式见 `docs/operations/runtime.md`。

## Consumers

REST/OpenAPI 是规范接口。CLI 和 MCP 是薄适配器，必须调用同一 API 或 Application Service，不能复制匹配逻辑和数据库访问。产品页面、首页快速查询、Excel 询价、OpenClaw 兼容接口和 v1 已共用 Product/Inquiry Service；消费者合同测试覆盖 OpenClaw、Hermes、WorkBuddy 使用的字段、artifact 所有权和错误码。
