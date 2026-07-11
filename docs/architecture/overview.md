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

当前路由仍直接访问 `app.database`，模板仍是独立完整文档。这些文件登记在 `policy/legacy_allowlist.json`。检查器允许债务继续存在，但禁止新增同类文件；每次迁移完成必须删除白名单项。

`templates/base.html` 已建立，`system_updates.html` 是第一张完成迁移的协议页面。新增页面不能进入白名单。现有内联脚本、样式、旧 API、路由数据库导入/SQL、异常文本外泄和 daemon 线程按具体文件、端点或出现次数登记；新增一处也会失败，白名单只能实质缩小。

建议迁移顺序：

1. 抽出 API Principal、错误映射和审计上下文。
2. 以报价模块作为首个完整纵向切片，移除 `quotes.py -> internal_api.py` 依赖。
3. 抽出产品 Repository 和 Inquiry Service，Web 与 AI API 复用同一用例。
4. 迁移合同、材料、发货和管理模块。
5. `app/database.py` 只保留连接基础设施，最终按领域拆除。

## Technology Triggers

- SQLite 保留，直到出现多主机写入、持续锁冲突或需要复杂并发事务。
- SQLAlchemy/Alembic 只在决定迁移数据库或 Repository 收益明确时引入。
- Redis/RQ 在 AI 长任务成为正式生产能力时引入；此前也不得新增 daemon 线程。
- React/Vue 只有在服务端渲染无法满足明确交互需求且有 ADR 时考虑。

## Runtime And Quality Gates

- 容器先运行 `scripts/init_database.py`，成功后才启动 Gunicorn worker；迁移仍用 SQLite 写事务防止其他入口并发初始化。
- `uv run python scripts/verify.py` 是本机与 CI 共用入口。
- Ruff 当前阻断语法错误、未使用导入和未使用变量。历史导入排序与全量类型检查暂不作为阻断项，完成基线清理后再通过 ADR/配置收紧。
- 结构约束通过 `policy/legacy_allowlist.json` 做差异棘轮，不能用新增白名单项绕过检查。
- `docs/governance/enforcement-matrix.md` 为每条宪章规则登记当前门禁和下一步；检查器验证规则编号没有漏项。
