# Runtime Operations

## Process Model

生产运行由两个长期进程组成：

```text
Web     -> Flask/Gunicorn，接收页面和 API 请求、提交持久任务
Worker  -> scripts.run_worker，领取任务、刷新租约/心跳、写进度与结果
```

两者必须使用同一份 `.env`、SQLite 数据库、`uploads/` 和 `outputs/`。货物识别 Web 页面已退役；保留的命令行/服务任务仍只由独立 Worker 执行，不进入 Web 请求线程。

## Local Start

先初始化迁移，再分别启动 Web 和 Worker：

```bash
uv run python -m scripts.init_database
uv run python app.py
```

另开一个终端：

```bash
uv run python -m scripts.run_worker
```

只处理一个排队任务后退出：

```bash
uv run python -m scripts.run_worker --once
uv run python -m scripts.run_worker --once --job-id <job_id>
```

## Container Start

Web 与 Worker 应一起构建和启动：

```bash
docker compose up -d --build bld-matcher bld-worker
docker compose ps
```

`bld-matcher` 的 healthcheck 调用 `/health/ready`；readiness 与 `bld-worker` healthcheck 都使用只读 SQLite 连接，不创建数据库或执行迁移。初始化只由 `scripts.init_database` 完成。不要只看到容器为 running 就判定可用。

## Health And Deployment Probe

```bash
curl -fsS http://127.0.0.1:5055/health/live
curl -fsS http://127.0.0.1:5055/health/ready
uv run python -m scripts.runtime_probe --base-url http://127.0.0.1:5055
uv run python -m scripts.worker_health
```

`/health/ready` 需要以下检查全部成功：

- SQLite 可连接；
- 所有迁移已应用；
- 至少存在一个启用中的管理员，产品表可查询；
- `BLD_REQUIRE_WORKER=1` 时，Worker 心跳未超过 `BLD_WORKER_STALE_SECONDS`。

`/health/live` 成功但 `/health/ready` 返回 503，表示进程还活着但不应接流量。先看响应内的 `checks`，再检查迁移、管理员和 Worker 日志。

Worker 默认每 30 秒写一次空闲心跳，`BLD_WORKER_HEARTBEAT_SECONDS` 必须小于 `BLD_WORKER_STALE_SECONDS`。任务进度检查点仍会即时刷新心跳。单次心跳写入失败会记录结构化错误并让 readiness 随后降级，但不会中断已领取任务。

## Persistent Job Recovery

- Worker 异常退出后，运行中任务不会消失。租约超过 `BLD_JOB_LEASE_SECONDS` 后，下一 Worker 可重新领取并增加 attempt。默认租约 900 秒，最小 330 秒；进度点和每次 AI 尝试之间的检查点会续租。
- 收到 SIGTERM/SIGINT 后，Worker 不再领取新任务，并在当前受控检查点把任务重新排队且不消耗 attempt。Compose 保留 330 秒停机宽限期，覆盖单次 Provider 最长 300 秒的不可中断网络等待；不要下调该宽限期。
- 页面刷新后通过现有 `job_id` 恢复状态；API 消费者使用 `GET /api/v1/jobs/{job_id}`。
- 取消接口只设置受控取消状态。Handler 在进度点及 AI 重试之间检查取消，并执行自己的文件补偿。货物识别在输出和审计提交前设最终检查点；越过该提交点后，完成优先于迟到的取消请求。
- 达到 `max_attempts`、未知任务类型或不可恢复错误会进入失败终态并保留稳定错误码。
- 不要直接修改 `background_jobs` 来“解锁”任务；先确认 Worker 已停止，再依据日志、事件和租约时间决定恢复方式。

## AI Provider Configuration

Web 运行时只接受环境配置：

```text
SHIPMENT_VISION_API_KEY
SHIPMENT_VISION_BASE_URL
SHIPMENT_VISION_MODEL
SHIPMENT_VISION_ALLOWED_HOSTS
SHIPMENT_VISION_ALLOWED_MODELS
SHIPMENT_VISION_TIMEOUT_SECONDS
SHIPMENT_VISION_MAX_RETRIES
SHIPMENT_VISION_RETRY_BACKOFF_SECONDS
SHIPMENT_VISION_MAX_CONCURRENCY
SHIPMENT_VISION_INPUT_COST_PER_MILLION
SHIPMENT_VISION_OUTPUT_COST_PER_MILLION
SHIPMENT_VISION_USE_ENV_PROXY
```

生产地址使用 HTTPS，且 host/model 必须同时在精确白名单内。环境代理默认关闭；只有明确审计过代理路径后才设置 `SHIPMENT_VISION_USE_ENV_PROXY=1`。页面请求中的 provider、model 或 base URL 会被忽略。

每次逻辑调用写入 `ai_provider_calls`，包含任务、供应商、模型、状态、尝试次数、Token、耗时和估算费用；正常、失败与停机/取消中断都保留记录，不保存密钥和图片正文。Provider 响应正文上限为 2 MiB，重定向必须保持配置的 origin 且不能携带账号、查询参数或片段。

## Retention

先查看计划，不删除任何内容：

```bash
uv run python -m scripts.cleanup_runtime
```

人工确认计划后应用并写审计：

```bash
uv run python -m scripts.cleanup_runtime --apply --actor scheduled-retention
```

容器维护任务：

```bash
docker compose --profile maintenance run --rm bld-retention
```

保留期由以下环境变量配置：

```text
BLD_UPLOAD_RETENTION_DAYS
BLD_OUTPUT_RETENTION_DAYS
BLD_JOB_RETENTION_DAYS
BLD_BACKUP_RETENTION_DAYS
BLD_ARTIFACT_RETENTION_HOURS
BLD_IDEMPOTENCY_RETENTION_HOURS
BLD_AI_CALL_RETENTION_DAYS
BLD_HEARTBEAT_RETENTION_DAYS
```

清理器只处理受控 `uploads/`、`outputs/` 和备份目录。未过期 artifact 引用的输出文件，以及排队或运行任务声明的上传路径受保护。默认命令始终是 dry-run；不要把 `--apply` 加入未经审查的日常 shell alias。

## API Key Rotation

`BLD_API_KEY_ROTATION_DAYS` 默认 90 天。管理页在达到建议日期后标记“建议轮换”，但不会自动停用 Key。轮换步骤是先创建最小 Scope 的新 Key、更新调用方并验证，再单独停用旧 Key。

## Structured Logs

默认 `LOG_FORMAT=json`，`LOG_LEVEL=INFO`。日志包含时间、级别、logger、message，以及存在时的 request ID、endpoint、method、job ID/kind、稳定错误码和 AI 指标。不要把上传内容、密钥或绝对路径放入日志 extra。

## Deployment And Rollback

1. 对运行数据库做 SQLite 一致性备份，并确认运行数据目录不在 Git 操作范围内。
2. 更新代码后先执行 `scripts.init_database`，再同时更新 Web 与 Worker。
3. 等待两个 healthcheck 成功，并运行 `scripts.runtime_probe`。
4. 最后执行持久任务 Worker、识别 Handler、取消/完成和输出补偿的自动化验收；不再验收已退役的识别 Web 页面。

迁移 017 会把旧识别任务迁入通用任务表并删除旧表。迁移后回滚到旧代码必须同时恢复迁移前数据库备份；不要只回滚容器镜像。产品数据同步与 NAS 数据方向仍遵守 `AGENTS.md`，本手册不授权任何运行数据覆盖。
