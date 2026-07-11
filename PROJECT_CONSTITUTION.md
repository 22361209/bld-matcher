# BLD Project Constitution

本文件定义长期开发必须遵守的项目边界。`AGENTS.md` 负责告诉开发者和 AI 先做什么，本文件负责定义什么不能被破坏。所有 `MUST` 和 `MUST NOT` 应有自动检查；暂时无法自动检查的要求必须登记在 `docs/governance/enforcement-matrix.md`，写明当前证据和下一道门禁。

## 1. Architecture

- `ARCH-001 MUST`：项目保持模块化单体。Web、API 和 Worker 只能调用 Application Service，不能承载业务规则。
- `ARCH-002 MUST`：业务按产品、询价、报价、合同、材料、发货等领域组织；跨领域调用通过公开服务接口完成。
- `ARCH-003 MUST NOT`：新路由模块不得直接导入 `app.database`、`sqlite3` 或执行 SQL。
- `ARCH-004 MUST NOT`：路由模块不得互相导入。鉴权、错误、Schema 等共享能力必须位于平台层。
- `ARCH-005 MUST NOT`：未经 ADR，不得引入新 Web 框架、ORM、数据库、任务队列或前端框架。
- `ARCH-006 MUST`：现有架构债务只能保存在 `policy/legacy_allowlist.json`；新代码不得加入同类债务。完成迁移后必须删除对应白名单项。

依赖方向固定为：

```text
Web / API / Worker -> Application Service -> Domain
Application Service -> Repository / File / Job / AI Ports
Infrastructure -> Ports
```

Domain 不得导入 Flask、SQLite、文件路径配置或模型供应商 SDK。

## 2. Data And Files

- `DATA-001 MUST NOT`：运行数据库、上传、输出、图纸、图片、备份和密钥不得进入 Git 或 Docker 镜像层。
- `DATA-002 MUST NOT`：不得在未明确方向时用一端运行数据覆盖另一端数据。
- `DATA-003 MUST`：数据库备份必须使用 SQLite Backup API 或数据库原生一致性机制，不能复制活动中的 DB/WAL/SHM 组合。
- `DATA-004 MUST`：文件写入采用临时文件、校验、原子替换；跨文件操作必须记录并支持补偿回滚。
- `DATA-005 MUST`：Schema 迁移在 Web worker 启动前单进程执行，并对历史数据库 fixture 做升级验证。
- `DATA-006 MUST NOT`：应用启动不得静默修改既有用户密码、业务数据或 API Key。

## 3. Security And Mutations

- `SEC-001 MUST`：所有 UI 写路由声明登录或权限要求；新增 `/api/v1` 路由声明 Scope，旧 API 只能保留精确白名单端点。
- `SEC-002 MUST`：UI 写操作需要 CSRF；`/api/v1` 写操作需要 Bearer Principal、Schema 校验和幂等保护。
- `SEC-003 MUST`：权限、校验、事务、审计和错误映射构成一次写操作，不得只实现其中一部分。
- `SEC-004 MUST NOT`：请求参数不得决定外部 AI 供应商地址、密钥、代理或数据外发目标。
- `SEC-005 MUST NOT`：完整密钥不得长期明文存储；创建时只显示一次，之后仅保留哈希和可识别后缀。
- `SEC-006 MUST NOT`：客户端提供的 actor、IP 或用户名不得直接成为可信审计身份。
- `SEC-007 MUST NOT`：异常响应不得暴露本机绝对路径、SQL、密钥或内部堆栈。

## 4. Web Page Protocol

- `UI-001 MUST`：新完整页面继承 `base.html`，使用批准的页面结构和组件。
- `UI-002 MUST NOT`：新模板不得包含内联脚本、内联样式或 `onclick` 等事件属性。
- `UI-003 MUST`：筛选、分页、排序和当前视图进入 URL，可刷新、返回和分享。
- `UI-004 MUST`：页面覆盖加载、空、错误、无权限、只读、提交中和成功状态。
- `UI-005 MUST`：破坏性操作显示对象名称、影响和二次确认，并由服务端再次授权。
- `UI-006 MUST`：页面协议同时通过桌面、移动端、键盘焦点和长文本验收。

完整定义见 `docs/ui/page-protocol.md`。

## 5. API And AI

- `API-001 MUST`：新增机器接口位于 `/api/v1`，请求和响应由 Schema 定义并生成 OpenAPI。
- `API-002 MUST`：API Key 映射到不可伪造的 Principal、集成名称和最小权限 Scopes。
- `API-003 MUST`：写接口支持 `Idempotency-Key`；并发修订使用版本号或 `If-Match`。
- `API-004 MUST`：长任务返回 `202 + job_id`，通过统一任务接口查询、取消和获取结果。
- `API-005 MUST NOT`：API 不返回绝对文件路径；文件通过有权限和保留期的 artifact ID 访问。
- `API-006 MUST NOT`：OpenClaw、Hermes、WorkBuddy、MCP 或其他 AI 工具不得直接访问 SQLite。
- `API-007 MUST`：旧接口作为兼容适配器保留明确周期，破坏性变化需要 ADR 和消费者合同测试。

完整定义见 `docs/api/ai-contract.md`。

## 6. Operations And Observability

- `OPS-001 MUST NOT`：请求线程不得创建无法恢复的 daemon 后台任务。
- `OPS-002 MUST`：外部调用设置超时、有限重试、并发限制，并记录耗时、供应商、模型和费用信息。
- `OPS-003 MUST`：捕获宽泛异常时必须写结构化日志，并转换为稳定错误码。
- `OPS-004 MUST`：部署必须经过迁移、健康检查和最小业务探针；容器启动不等于服务可用。
- `OPS-005 MUST`：上传、输出、任务和备份必须有保留期与清理策略。

## 7. Change Governance

- `GOV-001 MUST`：每次用户可见、数据、权限、接口、配置或运维变化提交一个 `changes/*.json` 片段。
- `GOV-002 MUST`：架构、技术栈、外部数据流或兼容边界变化必须先写 ADR。
- `GOV-003 MUST`：提交前在锁定环境运行 `uv run python scripts/verify.py`；CI 使用同一入口。
- `GOV-004 MUST NOT`：必选检查未通过时不得合并或部署。
- `GOV-005 MUST`：`main` 应受保护；日常开发通过短期分支和审查合并。
- `GOV-006 MUST`：文档描述、自动检查和运行行为冲突时，先停止发布并修正唯一事实来源。

## 8. Definition Of Done

完成一项改动至少意味着：

1. 代码位于正确领域和层级，没有扩大 legacy allowlist。
2. 权限、数据、API、页面和外发影响已明确。
3. 对应单元、合同、迁移或 UI 回归已经运行。
4. 变更片段已添加；需要时 ADR 和当前状态文档已更新。
5. `uv run python scripts/verify.py` 通过，工作树只包含本次范围。
