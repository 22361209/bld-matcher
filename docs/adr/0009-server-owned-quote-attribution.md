# ADR 0009: Server-Owned Quote Attribution

- Status: accepted
- Date: 2026-07-18
- Owners: BLD

## Context

报价记录同时来自网页手工录入、Excel 导入和受控 Agent API。历史合同允许调用方提交 `quoted_by` 和 `source_type`，其中来源值混合了录入通道和客户材料类型，网页还要求人工填写报价人、来源、原文及服务器附件路径。这些字段既增加录入负担，也允许客户端把系统归因改成任意值。

## Decision

1. `quoted_by` 和 `source_type` 改为服务端维护的创建归因：网页使用当前登录账号和 `manual`，Excel 导入使用确认导入的账号和 `excel`，API 使用 API Key 对应的 Principal 和 `api`。
2. 网页新增及修正表单不再显示报价人、来源、原文和附件路径；报价列表不再显示原文和附件路径。历史数据库列和已有数据保留。
3. API v1 和旧 `/api/quotes` 创建接口继续接受历史 `quoted_by`、`source_type` 输入以便旧调用方平滑迁移，但服务端忽略它们。OpenAPI 将 v1 创建字段标记为废弃。
4. API v1 修订合同移除 `quoted_by` 和 `source_type`；旧 API 及 Application Service 对任何修订归因字段的请求返回校验错误。报价创建归因创建后不可变。
5. API 响应的 `source_type` 枚举新增 `api`。依赖封闭枚举的消费者必须在部署前升级；未升级消费者继续使用兼容接口创建不会影响报价写入，但读取到 `api` 时必须按未知枚举安全处理。

## Compatibility

这是一项有意的行为变化，不标记为完全兼容：历史创建载荷仍可提交，但其自报归因不再生效；修订归因字段会被拒绝；响应可能返回新的 `api` 枚举值。发布说明、OpenAPI 快照和消费者合同测试必须一起更新。

## Consequences

- 报价页面只保留业务录入字段，操作人员无需填写系统可推导的信息。
- 报价人和创建来源可以作为可信展示字段；实际写入和修订 actor 仍由审计日志及 Principal 单独记录。
- `source_text` 和 `attachment_path` 暂时作为历史兼容字段保留，不在网页展示，也不由网页或 Excel 新增记录写入。

## Verification

- 页面回归确认新增和修正弹窗不存在四个已移除字段，列表不存在原文和附件路径。
- Web、Excel、旧 API 和 API v1 创建回归确认服务端归因覆盖客户端自报值。
- Application Service、旧 API PUT 和 API v1 PATCH 回归确认创建归因不可修订。
- OpenAPI 快照确认创建字段标记为废弃、PATCH 不再公开系统归因字段，并公开 `api` 来源值。
