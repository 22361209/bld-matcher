# Governance Enforcement Matrix

本表防止宪章只停留在文字层。`自动` 表示合并前可机器阻断，`部分自动` 表示已冻结新增债务但旧债仍需迁移，`人工/待建` 必须写明当前验收和下一道自动门禁。规则、实现或状态变化时同步更新本表。

| Rule | Status | Current evidence or gate | Next gate |
| --- | --- | --- | --- |
| `ARCH-001` | 部分自动 | API 平台层已抽出；路由依赖和 SQL 数量棘轮 | 报价纵向切片建立 Service/Domain 导入规则 |
| `ARCH-002` | 人工/待建 | ADR 和架构评审 | 模块依赖图检查 |
| `ARCH-003` | 自动 | 新路由数据库导入失败；旧 SQL 调用按文件计数只减不增 | 逐域把计数降为零 |
| `ARCH-004` | 自动 | 跨路由导入已清零，检查器禁止新增 | 无 |
| `ARCH-005` | 部分自动 | 技术栈/核心协议文件变化必须带 ADR | 增加依赖分类和许可证检查 |
| `ARCH-006` | 自动 | 白名单相对 HEAD/base ref 不得新增或增加计数 | 持续缩小 |
| `DATA-001` | 自动 | Git 跟踪路径与 Docker ignore 检查 | 增加构建镜像内容审计 |
| `DATA-002` | 人工/待建 | `AGENTS.md` 数据方向确认规则 | 同步包加入 source/target 确认记录 |
| `DATA-003` | 部分自动 | 产品同步使用 SQLite Backup API 并有完整性测试 | 抽成平台备份端口统一复用 |
| `DATA-004` | 部分自动 | 产品同步原子复制和媒体补偿回滚测试 | 文件端口合同测试 |
| `DATA-005` | 部分自动 | 容器预初始化、跨进程迁移测试、迁移记录测试 | 增加真实历史数据库 fixture 矩阵 |
| `DATA-006` | 自动 | 管理员密码不改写测试；API Key 明文删除迁移测试 | 对启动写操作做集中审计 |
| `SEC-001` | 自动 | UI 写路由鉴权、旧 API 精确端点、`/api/v1` 强类型 Principal/Scope 门禁 | 持续维护 Scope 最小权限 |
| `SEC-002` | 部分自动 | UI 全局 CSRF；v1 Scope、Pydantic Schema、幂等合同测试和写路由门禁 | 领域写接口逐项接入 |
| `SEC-003` | 部分自动 | v1 幂等装饰器自动记录 Principal、request ID 与结果；用例测试 | 报价 Service 建立事务与 before/after 审计模板 |
| `SEC-004` | 部分自动 | Web 识别忽略请求模型/base URL 的回归测试 | AI Provider 配置 Schema 和 egress allowlist |
| `SEC-005` | 自动 | Key 单次显示、`no-store`、哈希、到期校验和删除明文字段测试 | 阶段 6 增加轮换提醒 |
| `SEC-006` | 自动 | v1 与兼容 API 共用服务端强类型 Principal；伪造 actor 回归测试 | 无 |
| `SEC-007` | 部分自动 | v1 统一稳定错误码并隐藏异常文本；旧异常外泄计数只减不增 | 逐域将旧响应接入统一映射 |
| `UI-001` | 自动 | 新页面必须继承 `base.html` 并声明 page id/type | 逐页迁移旧模板 |
| `UI-002` | 自动 | 内联脚本/事件/样式按出现次数只减不增 | 旧计数降为零 |
| `UI-003` | 人工/待建 | 页面协议和现有筛选测试 | Playwright 刷新/返回/分享合同 |
| `UI-004` | 人工/待建 | 页面级回归测试 | 每种 page type 的状态 fixture |
| `UI-005` | 部分自动 | 权限测试和现有二次确认测试 | 共享 ConfirmDialog 行为测试 |
| `UI-006` | 人工/待建 | 代码评审 | 桌面/移动/键盘/长文本 Playwright 基线 |
| `API-001` | 自动 | 新 API 必须位于 v1、声明 Pydantic/OpenAPI 操作；路径与 Scope 一致性门禁和快照测试 | 领域 Schema 持续登记 |
| `API-002` | 自动 | 强类型 Principal、scopes/expiry 迁移、默认只读和授权测试 | 阶段 6 增加轮换运维 |
| `API-003` | 部分自动 | v1 写路由必须声明持久幂等；重放、冲突和审计合同测试 | 报价修订增加 If-Match 版本合同 |
| `API-004` | 人工/待建 | 当前 daemon 任务被冻结为一项遗留债务 | 持久任务表/队列与 jobs API |
| `API-005` | 部分自动 | 新能力只能进 v1；旧绝对路径接口被冻结 | artifact 存储和授权下载测试 |
| `API-006` | 部分自动 | 文档、架构边界和路由数据库棘轮 | AI 消费者合同测试禁止 DB 驱动 |
| `API-007` | 自动 | 旧 API 精确到函数和路径白名单 | 消费者迁移后删除兼容端点 |
| `OPS-001` | 部分自动 | daemon Thread 按 AST 计数为 1 且禁止增加 | 货物识别迁入持久任务执行器 |
| `OPS-002` | 人工/待建 | 货物识别参数和结果测试 | Provider 端口统一超时/重试/费用指标 |
| `OPS-003` | 部分自动 | v1 未处理异常写结构化 request ID 日志并返回稳定错误码；旧外泄计数不增 | 旧领域统一错误映射 |
| `OPS-004` | 部分自动 | 容器迁移前置和 `/login` 健康检查 | 最小业务探针和部署后验收脚本 |
| `OPS-005` | 人工/待建 | 当前目录约定 | retention 配置、dry-run 和清理审计 |
| `GOV-001` | 自动 | 代码/配置/页面变化必须有 JSON change fragment | 发布汇总器 |
| `GOV-002` | 部分自动 | 核心文件变化必须有 ADR | 扩大 ADR 触发器覆盖外部数据流 |
| `GOV-003` | 自动 | 本机和 CI 共用 `scripts/verify.py` | 无 |
| `GOV-004` | 自动 | GitHub Actions 失败阻断检查 | 在 GitHub 开启 required check |
| `GOV-005` | 人工/待建 | 短期分支规则 | GitHub branch protection 管理设置 |
| `GOV-006` | 部分自动 | 宪章、矩阵、变更片段和运行测试同一验收入口 | 文档示例可执行检查 |
