# ADR 0003: Quote Vertical Slice

- Status: accepted
- Date: 2026-07-11
- Owners: BLD

## Context

报价页面、Excel 导入和机器 API 原先全部位于 `app/routes/quotes.py`，路由直接连接 SQLite，并从另一个路由模块导入 API 鉴权。校验、事务、修订审计和响应转换混在适配器内，无法安全增加 API v1 幂等、并发版本控制或供多个消费者复用。

报价被选为第一个完整领域纵向切片，用于证明项目宪章中的 Domain/Service/Repository/Web/API 依赖方向可以在保持既有功能和 URL 的前提下落地。

## Decision

1. 报价领域迁入 `app/modules/quotes/`，由纯 `domain.py` 负责校验和值对象，`service.py` 负责用例、事务、审计和并发语义，`repository.py` 负责 SQLite，`web.py` 与 `api.py` 只做输入输出适配。
2. Web 页面、Excel 导入、旧 `/api/quotes*` 和新 `/api/v1/quotes*` 全部调用同一个 `QuoteService`，不复制报价规则。
3. `quote_records` 增加单调递增的 `version`。API v1 `PATCH` 必须同时提供 `Idempotency-Key` 和 `If-Match`；版本不匹配返回 `412 quote.version_conflict`，不会静默覆盖。
4. API v1 创建、读取和修订返回 ETag；Pydantic Schema 和 OpenAPI 描述请求、响应、Scope、幂等头、If-Match 与路径/查询参数。
5. API v1 不接受或返回本机附件路径。旧 API 暂时保留原字段和响应壳，作为精确登记的兼容适配器；消费者迁移完成前不删除。
6. 修订事务同时写入 before/after、版本、服务端 Principal 和操作日志。客户端 `on_behalf_of` 仅作为 API 平台审计说明，不替代真实 actor。
7. 模块依赖方向加入项目合同检查：Domain 禁止 Flask、SQLite、路径和供应商依赖，Service 禁止 Flask/SQLite，Web/API 禁止直接数据库导入。

## Alternatives

- 只把 SQL 函数移到另一个公共文件：路由仍会承担事务和业务规则，不能成为后续领域模板。
- 立即删除旧报价 API：会中断 Hermes 和已有脚本，不符合兼容策略。
- 仅依赖 `updated_at` 做并发控制：秒级时间戳不能可靠区分并发修订，整数版本更明确。
- 在 API 适配器中实现幂等和修订：Web、导入和未来 Worker 会继续产生不同业务语义。

## Consequences

- 报价成为后续产品、询价、合同等模块迁移的参考实现。
- 旧消费者无需立即修改；新消费者应使用 `/api/v1/quotes`、Scopes、幂等键和 ETag。
- 数据迁移只增加 `version` 并为历史记录回填 `1`，不改写报价内容。
- 报价页面模板仍属于历史页面协议债务，将在阶段 5 迁入 `base.html`。

## Verification

- `tests/test_quotes_module.py` 覆盖纯领域校验、事务、审计、导入、历史迁移和并发覆盖保护。
- `tests/test_app.py` 同时覆盖旧页面/旧 API 兼容与 API v1 幂等、Scope、ETag、If-Match、OpenAPI 和路径隐藏。
- `scripts/check_project_contract.py` 验证模块层依赖、v1 Scope/Schema/幂等/OpenAPI，并确认旧 API 只是原端点数量不变的适配器迁移。
