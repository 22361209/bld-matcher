# ADR 0001: Project Governance And Stack Baseline

- Status: accepted
- Date: 2026-07-11
- Owners: BLD

## Context

项目已经覆盖询价、产品、报价、合同、材料、发货、图片和 AI 调用，但早期代码以路由直连 SQLite、独立 HTML 文档和长篇交接说明为主。继续增加功能会放大页面漂移、跨模块耦合、运行数据误入镜像、接口不兼容和 AI 直连数据库等风险。

当前团队规模、部署方式和并发量不需要微服务或 SPA。优先目标是固定可长期演进的边界，并让违反边界的新增代码在提交前失败。

## Decision

1. 采用 Python 3.12、Flask 3、Jinja、SQLite 和文件系统组成的模块化单体；依赖由 `pyproject.toml` 与 `uv.lock` 锁定。
2. 目标依赖方向为 Web/API/Worker -> Application Service -> Domain -> Ports，基础设施实现 Ports。路由不得新增 SQLite 直连或跨路由导入。
3. 页面统一继承 `templates/base.html`，声明稳定的 `page_id` 和 `page_type`，禁止新增独立文档壳、内联脚本和内联样式。
4. 机器接口以 `/api/v1`、Schema、OpenAPI、Principal、Scope、幂等键、任务和 artifact 为长期合同。现有 `/api/internal/*` 与 `/api/quotes` 仅作为登记在案的兼容接口。
5. AI 工具只能调用认证 API 或同一 Application Service；请求不能覆盖供应商地址、密钥或模型白名单，AI 不得直接访问 SQLite。
6. Docker 启动在 Gunicorn worker 前执行数据库初始化；迁移本身同时保持跨进程安全。
7. `scripts/verify.py` 是本机和 CI 的唯一必选验收入口。现有结构债务进入只减不增的白名单，Ruff 先阻断语法和未使用代码；导入排序和全量类型检查按基线逐步收紧。
8. 路由适配器必须按页面或用例职责组织，单文件上限为 320 行和 15 个 endpoint；禁止用动态路由注册绕过静态权限、API 与结构检查。

## Alternatives

- 立即拆微服务：当前部署与团队复杂度不足以抵消分布式事务、运维和接口治理成本。
- 立即迁移 React/Vue：现有主要流程适合服务端渲染，先统一页面协议比重写前端风险更低。
- 立即迁移 PostgreSQL/ORM：当前主要问题是边界和并发初始化，不是 SQLite 能力上限。
- 只写开发文档：无法阻止后续开发或 AI 在压力下重复旧模式，因此必须配套机器检查和 CI。

## Consequences

- 新功能需要先选择领域、页面类型和 API 合同，短期增加少量设计工作。
- 旧路由和模板不会一次性重写；白名单明确技术债务并要求迁移后删除。
- 技术栈、核心协议和外部 AI 数据流变化需要新的 ADR。
- 当出现多主机写入、持续锁冲突、正式长任务队列或 SSR 无法承载的交互需求时，再用后续 ADR 评估数据库、队列或前端框架。

## Verification

- `scripts/check_project_contract.py` 检查页面继承、内联资源、路由依赖与容量、写路由鉴权、API 版本、Scope、运行数据和白名单增量。
- `scripts/verify.py` 检查项目合同、锁文件、Ruff、语法和完整回归测试。
- GitHub Actions 在 pull request 和 main push 上运行同一入口。
- 第一张协议页面 `system_updates.html` 已继承基础模板，证明棘轮可以开始缩小。
