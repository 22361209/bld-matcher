# ADR 0008: Product Price API And Web Module Removal

- Status: accepted
- Date: 2026-07-16
- Owners: BLD

## Context

产品目录导入已可携带并逐条确认“导入单价”，网页端独立的“维护单价”Excel 上传、预览和确认流程与该入口重复。保留两个网页写入渠道会增加权限、校验和审计面，也不能满足 AI Agent 必须经认证 API 写入的边界。

## Decision

1. 删除网页“维护单价”按钮、`/prices/import` 及其预览/确认路由和解析模块，不保留重定向兼容层。
2. 增加 `products:write` 最小权限 Scope，并新增 `POST /api/v1/products/{product_id}/price`。该接口只更新含税单价，使用 Pydantic 请求/响应模型、Bearer Principal、`Idempotency-Key`、OpenAPI 登记和服务端审计。
3. 调用方先读取产品，再将读取结果的 `updated_at` 作为 `expected_updated_at` 提交；不一致时返回稳定的 `product.version_conflict`，避免旧读取结果覆盖后续更新。
4. API、AI Agent 和其他机器调用继续经 `ProductService`，不获得 SQLite 或运行时文件访问权限。

## Alternatives

- 保留网页单价导入：与产品目录导入重叠，继续扩大人工批量写入入口。
- 让 AI Agent 直接写 SQLite：违反 API/AI 边界，且绕过 Scope、审计、幂等和并发保护。
- 将单价写入混入产品搜索接口：读写权限无法最小化，OpenAPI 合同也不清晰。

## Consequences

- 管理员不再有网页端单价 Excel 维护入口；需要人工批量调整时使用目录导入的冲突预览确认流程。
- 新 API Key 默认不包含 `products:write`，管理员必须显式授予；历史 Key 按既有兼容 Scope 规则保持可用。
- 机器调用需要保留产品 ID 与 `updated_at`；并发冲突应重新读取后再用新的幂等键提交。

## Verification

- `tests/test_app.py` 覆盖网页路由已移除、Scope 拒绝、API 写入、幂等重放、版本冲突和 Principal 审计。
- `scripts/openapi_snapshot.py --check` 与 `scripts/route_snapshot.py --check` 验证机器合同和路由合同。
- `uv run python scripts/verify.py` 验证工程协议、静态检查、全量测试与快照。
