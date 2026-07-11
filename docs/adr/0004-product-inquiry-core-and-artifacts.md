# ADR 0004: Product And Inquiry Core With Principal-Owned Artifacts

- Status: accepted
- Date: 2026-07-11

## Context

产品查询、首页快速号码查询、网页询价和 OpenClaw 内部接口此前分别读取数据库或构造 `ProductCatalog`，快速查询与机器人接口还各自维护一份 BLD/OE/品牌号容错规则。产品、询价和首页三个路由适配器直接依赖 `app.database`，无法证明 Web 与 AI 消费者执行的是同一套业务语义。

旧询价导出通过绝对 `output_path` 交付文件。这个字段必须为现有 OpenClaw 保留，但不能继续进入新 API；新消费者需要可授权、可过期、不可猜测且不暴露服务器路径的文件资源。

## Decision

1. 建立 `app/modules/products/`：`ProductService` 负责用例与缓存失效，`SQLiteProductRepository` 负责产品查询、维护、目录快照和事务内媒体写入。首页与产品页面通过服务访问，新增 `GET /api/v1/products/search`。
2. 建立 `app/modules/inquiry/`：首页快速查询、网页 Excel 预览/导出、人工映射、旧 `/api/internal/inquiry/*` 和新 `/api/v1/inquiries/*` 共用 `InquiryService`、`WorkbookInquiryEngine` 与产品目录端口。
3. 产品目录缓存版本同时观察产品/别名计数、更新时间和 SQLite DB/WAL/SHM 文件签名，兼容同一秒内的直接兼容写入；领域迁移完成后再移除对文件签名的兼容观察。
4. 新增 `api_artifacts` 元数据表和 `SQLiteArtifactStore`。artifact 使用随机 ID，绑定 `ApiPrincipal.subject`，默认 24 小时过期，数据库内部保存受控输出路径和 SHA-256；API 响应只返回 ID、文件名、大小、校验值、到期时间和下载 URL。
5. `GET /api/v1/artifacts/{id}` 只允许创建该 artifact 的 Principal 下载。不存在、过期和不属于当前 Principal 使用同一 404 错误，避免资源枚举。
6. `contracts/openapi-v1.json` 成为提交的机器合同快照，`scripts/openapi_snapshot.py --check` 进入统一 `verify`。消费者行为测试同时调用旧 OpenClaw 接口和 v1，证明关键匹配结果一致。

## Compatibility

- `/api/internal/inquiry/numbers`、`/file`、`/analyze` 的 URL、认证方式、响应字段、`output_path` 和文件命名继续兼容。
- 网页 `/`、`/match*`、`/manual-map*`、`/products*` 的 URL 与表单字段不变。
- v1 不接受服务器 `file_path`，也不返回绝对路径。当前 v1 先覆盖号码/文字分析与导出；文件上传资源在具备上传 artifact 合同后再新增。
- 旧的 `app.database` 产品函数暂作测试和其他未迁移领域的兼容 facade，阶段 6 才删除。

## Consequences

- 产品、询价和 AI 适配器已有可独立测试的事务边界，四个旧路由模块从数据库直连白名单移除。
- artifact 元数据会增长，需要阶段 6 的统一保留期任务删除过期记录和无引用文件。
- OpenAPI 变更必须先判断兼容性并显式更新快照，不能由代码生成过程静默改写。
- 当前 Excel 引擎仍是同步执行；长文件和视觉识别在阶段 6 迁入持久任务执行器。
