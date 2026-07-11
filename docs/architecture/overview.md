# Architecture Overview

## Decision

BLD 采用 Python 3.12 上的模块化单体。当前继续使用 Flask、Jinja、SQLite 和文件系统，不进行微服务、SPA 或数据库大迁移。依赖由 `pyproject.toml` 和 `uv.lock` 锁定；目标是先建立明确依赖方向，再逐域移动现有代码。正式决定见 `docs/adr/0001-project-governance-and-stack-baseline.md`。

## Target Layers

```text
app/
  platform/              # auth, db, files, jobs, audit, AI adapters
  modules/
    products/
    inquiry/
    quotes/
    contracts/
    materials/
    shipping/
  web/                   # shared page shell and components
  api/v1/                # auth, errors, OpenAPI assembly
```

每个业务模块逐步拥有：

```text
domain.py                # 纯业务规则和值对象
service.py               # 用例、事务、权限和审计编排
repository.py            # 持久化接口与 SQLite 实现
schemas.py               # Web/API 输入输出模型
web.py                   # Blueprint 和页面适配
api.py                   # /api/v1 适配
```

## Dependency Rules

- Web、API、CLI、Worker 是输入适配器，只调用 Service。
- Service 可以调用 Domain 和 Ports，负责一次用例的事务边界。
- Domain 不依赖 Flask、SQLite、Path 或供应商 SDK。
- Repository、文件存储、队列和 AI Provider 是基础设施实现。
- 一个模块不得导入另一个模块的路由、模板或数据库实现。

## Current Debt And Ratchet

合同、材料、发货通知和后台管理已经迁入各自模块，页面独立文档与模板内联资源债务清零。当前只剩登录、产品数据同步和货物识别三个运行/兼容适配器直接访问 `app.database`；它们登记在 `policy/legacy_allowlist.json`，检查器允许债务继续存在但禁止增长。跨路由导入已清零。

全部完整页面继承 `templates/base.html` 并声明唯一 page ID 和 page type；独立页面、内联脚本/事件和内联样式白名单均为空。旧 API、剩余路由数据库导入/SQL、异常文本外泄和 daemon 线程仍按具体文件、端点或出现次数登记，新增一处即失败，白名单只能实质缩小。

当前落地状态与迁移顺序：

1. 已完成：`app/platform/` 提供 API Principal、Scope、请求 ID、稳定错误、Pydantic Schema、OpenAPI、幂等和审计上下文；`app/api/v1/` 已建立版本入口。
2. 已完成：`app/modules/quotes/` 是首个 Domain/Service/Repository/Web/API 纵向切片；页面、导入、旧 API 和 v1 共用 Service，路由数据库直连已清零，修订使用整数版本和 If-Match。
3. 已完成：`app/modules/products/` 和 `app/modules/inquiry/` 提供产品 Repository、目录快照、询价 Service、Excel 引擎和 v1/旧 API 适配；首页、产品页、网页询价与 AI 消费者共用业务内核。
4. 已完成：Principal 所有权、24 小时到期和 SHA-256 校验的 artifact 下载边界；OpenAPI 提交快照进入统一验收。
5. 已完成：`app/modules/contracts/`、`materials/`、`shipping/` 和 `admin/` 负责事务、审计与文件补偿；全部页面纳入基础模板协议，模板内联资源清零。
6. 下一步：完成持久任务、AI Provider 端口、运行治理和剩余历史债务清零，使 `app/database.py` 最终只保留连接与迁移基础设施。

## Technology Triggers

- SQLite 保留，直到出现多主机写入、持续锁冲突或需要复杂并发事务。
- SQLAlchemy/Alembic 只在决定迁移数据库或 Repository 收益明确时引入。
- Redis/RQ 在 AI 长任务成为正式生产能力时引入；此前也不得新增 daemon 线程。
- React/Vue 只有在服务端渲染无法满足明确交互需求且有 ADR 时考虑。

## Runtime And Quality Gates

- 容器先运行 `scripts/init_database.py`，成功后才启动 Gunicorn worker；迁移仍用 SQLite 写事务防止其他入口并发初始化。
- `uv run python scripts/verify.py` 是本机与 CI 共用入口。
- Pydantic 2 是 `/api/v1` Schema 与 OpenAPI 的唯一模型工具；Flask 仍是唯一 Web 框架。
- `/api/v1` 写路由必须声明 Scope、Pydantic Schema、幂等保护和 OpenAPI 操作，平台层从服务端 Principal 生成审计身份。
- 项目合同检查模块层依赖方向；报价模块是后续领域必须优先复用的实现模板。
- 产品目录缓存同时观察 DB/WAL/SHM 签名，兼容尚未迁移的直接写入；新产品和询价适配器已无数据库导入。
- 页面合同检查阻断独立 HTML、无效 page type、内联代码和重复 page ID；产品、材料和物料图纸脚本按 `data-page` 独立初始化。
- `scripts/openapi_snapshot.py --check` 阻断未审查的 API 路径、Schema、Scope、参数和响应漂移。
- Ruff 当前阻断语法错误、未使用导入和未使用变量。历史导入排序与全量类型检查暂不作为阻断项，完成基线清理后再通过 ADR/配置收紧。
- 结构约束通过 `policy/legacy_allowlist.json` 做差异棘轮，不能用新增白名单项绕过检查。
- `docs/governance/enforcement-matrix.md` 为每条宪章规则登记当前门禁和下一步；检查器验证规则编号没有漏项。
