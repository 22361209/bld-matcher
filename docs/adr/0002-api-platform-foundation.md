# ADR 0002: API Platform Foundation

- Status: accepted
- Date: 2026-07-11
- Owners: BLD

## Context

旧 `/api/internal/*` 与 `/api/quotes` 在各自路由中处理 Bearer Key、错误格式和审计身份，报价路由甚至从询价 API 路由导入鉴权装饰器。该结构无法为长期 AI 应用提供稳定 Principal、细粒度 Scope、Schema、OpenAPI、请求追踪和安全重试，也会让每个新领域复制一套边界代码。

## Decision

1. 在 `app/platform/` 建立共享 API 平台，统一负责 `ApiPrincipal`、Bearer 认证、Scope、请求 ID、稳定错误信封、Schema 校验、OpenAPI 注册、幂等存储和 API 写审计。
2. 引入 Pydantic 2 作为 `/api/v1` 请求、响应和 OpenAPI Schema 的唯一模型工具，不引入额外 Web 框架或 ORM。
3. API Key 增加 `scopes` 与 `expires_at`。历史 Key 迁移为全部兼容 Scope，避免已有消费者中断；新 Key 默认只有读取元数据、产品、询价、artifact 和报价的权限，写 Scope 必须单独选择。
4. `/api/v1` 成功和失败响应都携带 `api_version` 与 `request_id`；未处理异常只返回稳定错误码，内部异常通过结构化日志记录。
5. `/api/v1` 写路由必须同时声明 `api_scope_required` 与 `idempotency_required`。幂等记录以 Principal、端点、方法和 Key 为唯一维度，保存请求摘要与可重放响应，并自动记录服务端 Principal 审计。
6. 每个 `/api/v1` 操作必须登记 OpenAPI，Scope 必须与路由声明一致；项目合同检查器在提交前验证这些规则。
7. 旧 API 继续使用同一 Principal 认证，但暂时保留原响应格式和行为；后续领域阶段把旧端点改为 `/api/v1` Application Service 的兼容适配器。

## Alternatives

- 继续在各路由复制装饰器：会重复认证、错误和审计逻辑，无法形成可靠机器合同。
- 引入 Flask-RESTX、Connexion 或新 Web 框架：当前只需要 Schema 和 OpenAPI 基础能力，额外框架会扩大迁移面。
- 让所有 Key 永久拥有全部权限：无法满足 AI 应用最小权限和密钥轮换要求。
- 立即删除旧 API：会破坏 OpenClaw、Hermes 和现有脚本，违反兼容适配器策略。

## Consequences

- 新 API 模块必须使用 Pydantic 模型、平台响应函数和 OpenAPI 登记，开发步骤更明确。
- API Key 页面需要显示并选择 Scope，可选配置到期日期；完整 Key 仍只显示一次。
- 旧 Key 保持能力不变，新 Key 默认只读；需要写入报价等能力时由管理员明确授权。
- 幂等记录会在 SQLite 中保留 24 小时，阶段 6 的统一保留期任务将负责集中清理策略。

## Verification

- `tests/test_api_platform.py` 覆盖 Scope、到期、请求 ID、Schema、稳定错误、幂等重放、冲突和审计身份。
- `tests/test_app.py` 覆盖旧 API 兼容、Key 管理、迁移回填、`/api/v1` 首页与 OpenAPI 文档。
- `scripts/check_project_contract.py` 阻断缺少 Scope、幂等保护、OpenAPI 登记或 Scope 不一致的 `/api/v1` 路由。
