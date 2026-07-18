# ADR 0006: Persistent Jobs, AI Provider And Runtime Governance

- Status: accepted
- Date: 2026-07-11
- Owners: BLD

## Context

货物识别原先由 Flask 请求启动 daemon 线程，任务状态保存在专用表中。进程重启会中断任务，请求进程既承担 HTTP 又承担长时间 AI 调用，也没有租约恢复、统一取消、结果所有权或独立健康证据。AI 参数、超时、重试、并发和费用记录分散在脚本中；上传、输出、任务、幂等记录和备份也没有统一保留期执行器。

项目目前是单主机/NAS 上的模块化单体，Web 和 Worker 共享同一 SQLite 数据库与文件卷。当前任务量不值得立即引入 Redis，但“暂不引入”不能继续成为不可恢复线程的理由。

## Decision

1. 使用 SQLite 建立通用 `background_jobs` 与 `background_job_events`。请求、公开进度和结果分开保存；状态、所有者、重试次数、运行时间、租约、取消请求和过期时间均持久化。
2. `scripts/run_worker.py` 是独立 Worker 入口。Worker 通过租约原子领取任务；进度点与 AI 尝试间检查点续租，空闲心跳限频。心跳失败降低健康证据但不终止已领取任务。过期租约可被下一 Worker 恢复。SIGTERM/SIGINT 停止领取任务，并在受控检查点重新排队而不消耗 attempt。Web 请求只入队，不创建后台线程。
3. `/api/v1/jobs/{id}`、`/result` 和 `/cancel` 是统一任务合同。读取需要 `jobs:read`，取消需要 `jobs:cancel` 和 `Idempotency-Key`；跨 Principal 的任务统一返回 404，内部请求载荷和路径不进入响应。
4. 货物识别作为第一个任务 Handler 迁入通用执行器。既有页面 URL、表单和结果下载保持兼容；刷新页面可用 `job_id` 恢复进度，排队或运行任务可请求取消。输出与审计前设置最终取消检查点并执行文件补偿；越过提交点后完成优先，避免已提交结果被标成取消。
5. AI 访问统一通过 Provider 端口。Web 运行时的供应商、URL、模型、密钥、代理和白名单只来自环境配置；只允许白名单 HTTPS 目标，HTTP 仅允许 loopback，重定向保持同 origin 并重新校验。Provider 统一执行超时、2 MiB 响应上限、有限指数退避、并发上限、稳定错误、Token/耗时/估算费用指标，并在重试之间响应 Worker 中断。
6. 每次逻辑 AI 调用写入 `ai_provider_calls`，任务与调用记录关联，成功、失败和停机/取消中断都有状态与尝试次数；结构化日志只记录受控字段，不记录密钥、图片或绝对输入路径。
7. `/health/live` 只证明进程响应；`/health/ready` 通过只读连接检查数据库、完整迁移、最小业务条件和新鲜 Worker 心跳，不创建数据库或执行迁移。未来时间异常的心跳不视为新鲜。部署探针还检查登录页可渲染。
8. 上传、输出、artifact、终态任务、幂等记录、AI 调用、Worker 心跳和备份使用统一保留期配置。清理默认只生成计划，必须显式 `--apply` 才删除，并写审计日志；活跃 artifact 文件和排队/运行任务声明的上传路径受保护。
9. 迁移 `017_runtime_jobs_ai_and_health` 将旧货物识别任务迁入通用表。已完成结果保留，迁移时仍未完成的旧线程任务标记为可解释的失败，旧专用表随后删除。
10. `app/database.py` 只保留 Schema、连接和迁移入口；业务查询和写入归属各领域 Repository/Persistence。路由数据库直连、daemon 线程和异常文本外泄的遗留计数降为零。

## Queue Choice And Upgrade Trigger

SQLite 队列是当前单主机部署的有意选择，不是通用分布式队列替代品。出现以下任一条件时，必须用新 ADR 评估 Redis/RQ、Celery 或外部队列，并保持现有 Job Service/API 合同：

- Web 与 Worker 分布在不能共享同一低延迟 SQLite 文件系统的多台主机；
- 任务领取或进度更新产生持续 SQLite 锁竞争；
- 需要独立扩缩多个高并发 Worker、优先级队列或计划任务；
- 任务吞吐、延迟或重试策略超出当前租约轮询模型。

## Compatibility

- `/shipment-recognition`、`/run`、`/status/<id>` 和既有下载流程继续可用。
- `/api/internal/*` 与 `/api/quotes` 继续作为精确登记的兼容适配器，不在本决定中删除。
- 产品数据同步、登录和货物识别的公开 URL/端点名称保持不变，只把实现迁入领域模块。
- API Key 轮换为管理页提醒，不自动删除现有 Key；到期时间和 Scope 的原有强制行为不变。

## Consequences

- Web 与 Worker 必须作为两个进程共同运行；Worker 缺失时 liveness 仍可成功，但 readiness 默认失败。
- 取消是协作式协议，不会强杀正在执行的单次外部调用；Compose 的 330 秒停机宽限期不得低于 Provider 300 秒超时上限加收尾余量。
- 迁移到 017 后不能只回滚旧 Web 代码，因为旧代码依赖已删除的专用任务表；需要恢复迁移前一致性备份，或继续向前修复。
- SQLite 仍是单机写入边界。租约提供进程故障恢复，不等于跨区域分布式一致性。
- 保留期执行器已具备 dry-run、路径边界和审计，但生产调度频率仍由部署环境决定。

## Verification

- `tests/test_runtime_governance.py` 覆盖并发原子领取、租约恢复、取消提交点、优雅停机重排、空闲心跳限频/失败隔离、私有载荷、AI egress/重试/中断审计/费用/并发、只读健康检查、共享 artifact/活跃路径保留、安全解压和三类历史数据库升级。
- `tests/test_app.py` 覆盖任务 Principal 所有权、Scope、幂等取消、结果未就绪、货物识别刷新/下载和既有 URL 兼容。
- `scripts/check_project_contract.py` 阻断 daemon、新路由数据库依赖、异常文本外泄、带角色后缀的模块反向依赖和 `app/database.py` 业务函数回流；`scripts/verify.py` 对平台与本阶段运行边界执行 Pyright 棘轮。
- `scripts/runtime_probe.py`、Docker Web healthcheck 和 Worker healthcheck 提供部署后运行证据。
