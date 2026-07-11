# Governance Enforcement Matrix

本表防止宪章只停留在文字层。`自动` 表示合并前可机器阻断，`部分自动` 表示已冻结新增债务但旧债仍需迁移，`人工/待建` 必须写明当前验收和下一道自动门禁。规则、实现或状态变化时同步更新本表。

| Rule | Status | Current evidence or gate | Next gate |
| --- | --- | --- | --- |
| `ARCH-001` | 部分自动 | 全部现有业务、登录、同步与货物识别均有 Service/Repository 边界；Worker 只调用 Service，行为测试覆盖事务与补偿 | 新领域继续复用模块合同测试 |
| `ARCH-002` | 部分自动 | 模块层依赖检查同时识别标准名和 `_domain/_service/_web/_api` 后缀，阻断反向依赖；Inquiry 仅经 ProductService 公开目录端口跨域 | 增加跨领域公开服务依赖图 |
| `ARCH-003` | 自动 | 路由数据库导入和 SQL 基线均为零；`app/database.py` 只允许 `connect()`，业务函数回流即失败 | 无 |
| `ARCH-004` | 自动 | 跨路由导入已清零，检查器禁止新增 | 无 |
| `ARCH-005` | 部分自动 | 技术栈/核心协议文件变化必须带 ADR | 增加依赖分类和许可证检查 |
| `ARCH-006` | 自动 | 白名单相对 base ref 不得新增或增加；路由 DB、SQL、daemon 和异常外泄计数已清零；聚集文件按行数冻结为只减不增；路由适配器硬限制 320 行/15 endpoint 并禁止动态注册 | 消费者迁移后删除兼容 API |
| `DATA-001` | 自动 | Git 跟踪路径与 Docker ignore 检查 | 增加构建镜像内容审计 |
| `DATA-002` | 人工/待建 | `AGENTS.md` 数据方向确认规则 | 同步包加入 source/target 确认记录 |
| `DATA-003` | 部分自动 | 产品同步使用 SQLite Backup API 并有完整性测试 | 抽成平台备份端口统一复用 |
| `DATA-004` | 部分自动 | 产品同步、材料 Excel、合同 PDF、发货模板/输出、物料图纸和识别输出具备原子写或失败补偿测试 | 抽取正式 FileStore 端口并扩大覆盖 |
| `DATA-005` | 自动 | 容器预初始化、跨进程迁移、迁移记录，以及 v000/v006/v012 三类历史数据库完整升级与数据保留 fixture | 新增关键历史形态时扩充矩阵 |
| `DATA-006` | 自动 | 管理员密码不改写测试；API Key 明文删除迁移测试 | 对启动写操作做集中审计 |
| `SEC-001` | 自动 | UI 写路由鉴权、旧 API 精确端点、`/api/v1` 强类型 Principal/Scope 门禁 | 持续维护 Scope 最小权限 |
| `SEC-002` | 部分自动 | UI 全局 CSRF；v1 Scope、Pydantic Schema、幂等合同测试和写路由门禁 | 领域写接口逐项接入 |
| `SEC-003` | 部分自动 | Service 负责事务与审计；文件失败补偿、任务终态/取消和平台幂等均有行为测试 | 为新增写用例复用同一验收矩阵 |
| `SEC-004` | 自动 | Web 忽略请求模型/地址；Provider 配置 Schema、host/model allowlist、HTTPS/重定向校验和默认禁用环境代理均有测试 | 新 Provider 必须先写 ADR 与 egress 测试 |
| `SEC-005` | 自动 | Key 单次显示、`no-store`、哈希、到期、明文字段删除和可配置轮换提醒均有测试；提醒不自动停用 | 无 |
| `SEC-006` | 自动 | v1 与兼容 API 共用服务端强类型 Principal；伪造 actor 回归测试 | 无 |
| `SEC-007` | 自动 | v1 使用稳定错误；路由宽异常统一结构化记录和安全响应，异常文本外泄基线为零 | 新适配器持续执行静态与行为门禁 |
| `UI-001` | 自动 | 全部完整页面继承 `base.html`，声明有效且唯一 page id/type；独立页面白名单为零 | 持续阻断漂移 |
| `UI-002` | 自动 | 模板内联脚本、事件和样式基线均为零；页面资产由所属模板加载，ES Module 按 data-page 初始化；全局 CSS/JS 行数只减不增并禁止已拆页面行为回流 | 持续扩大页面资产语义门禁 |
| `UI-003` | 人工/待建 | 页面协议和现有筛选测试 | Playwright 刷新/返回/分享合同 |
| `UI-004` | 人工/待建 | 页面级回归测试 | 每种 page type 的状态 fixture |
| `UI-005` | 部分自动 | 权限测试和现有二次确认测试 | 共享 ConfirmDialog 行为测试 |
| `UI-006` | 部分自动 | 静态页面协议和服务端渲染自动回归；阶段交付执行桌面/移动/键盘/长文本真实浏览器验收 | 将代表性浏览器流程纳入 CI |
| `UI-007` | 自动 | CSS 路径与模板所有权、基础/组件/页面 1400/1000/600 行上限、共享层业务词、ID 选择器、`@import`、新增 `!important` 和废弃跨域类名均由项目检查器阻断 | 引入 CSS 解析器时替换轻量选择器扫描，保持同一合同 |
| `API-001` | 自动 | 新 API 必须位于 v1、声明 Pydantic/OpenAPI 操作；报价、产品、询价和二进制 artifact 均有 Schema 与快照 | 领域 Schema 持续登记 |
| `API-002` | 自动 | 强类型 Principal、scopes/expiry、默认只读、授权和 90 天默认轮换提醒测试 | 无 |
| `API-003` | 自动 | v1 写路由强制持久幂等；报价 PATCH 强制整数 version、ETag 与 If-Match，并有并发覆盖测试 | 其余可修订资源复用版本合同 |
| `API-004` | 自动 | 通用持久任务/事件表、检查点续租、故障恢复、优雅停机重排、所有权隔离、jobs 状态/结果/幂等取消 API 和强类型 OpenAPI 快照 | 新任务类型复用统一 Handler 合同并声明最终提交点 |
| `API-005` | 自动 | v1 只返回 Principal 所有的限时 artifact；响应无绝对路径；跨 Key、过期、下载和保留期清理测试 | 无 |
| `API-006` | 自动 | 产品/询价 Web、旧 API 和 v1 共用服务；消费者合同验证同一结果且 artifact 禁止越权 | 扩大到合同和任务消费者 |
| `API-007` | 自动 | 旧 API 精确白名单；适配器迁移不增端点；报价和询价新旧合同测试；OpenAPI 快照阻断漂移 | 消费者迁移后删除兼容端点 |
| `OPS-001` | 自动 | daemon 计数为零；货物识别只入持久队列，Worker 租约恢复、限频心跳、取消提交点和优雅停机重排有测试 | 禁止新增进程内后台线程 |
| `OPS-002` | 自动 | Provider 统一超时、响应上限、有限指数重试、重试中断审计、并发上限、稳定错误和 Token/耗时/估算费用指标 | 增加供应商级预算告警 |
| `OPS-003` | 自动 | Web、API 和 Worker 使用结构化日志字段与稳定错误；异常文本外泄计数为零 | 将日志 Schema 接入集中采集验证 |
| `OPS-004` | 自动 | 无副作用 Web readiness 分项检查 DB/迁移/业务/Worker，Worker 独立 healthcheck，`runtime_probe` 校验 live/ready/login | 部署平台把 probe 设为发布阻断 |
| `OPS-005` | 部分自动 | 可配置保留期、默认 dry-run、受控路径、活跃 artifact 与任务上传保护、应用清理审计与测试 | 在生产调度 maintenance profile 并监控执行结果 |
| `GOV-001` | 自动 | 代码/配置/页面变化必须有 JSON change fragment | 发布汇总器 |
| `GOV-002` | 部分自动 | 核心文件变化必须有 ADR | 扩大 ADR 触发器覆盖外部数据流 |
| `GOV-003` | 自动 | 本机和 CI 共用 `scripts/verify.py`，阻断平台/运行边界类型漂移，并从隔离数据库精确比较 Flask 路由与 OpenAPI 提交快照 | 逐步把历史 Excel 与领域模块纳入 Pyright |
| `GOV-004` | 自动 | GitHub Actions 失败阻断检查 | 在 GitHub 开启 required check |
| `GOV-005` | 人工/待建 | 短期分支规则 | GitHub branch protection 管理设置 |
| `GOV-006` | 部分自动 | 宪章、矩阵、变更片段和运行测试同一验收入口 | 文档示例可执行检查 |
