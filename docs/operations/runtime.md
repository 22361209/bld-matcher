# Runtime Operations

## Process Model

生产运行是单个长期进程：

```text
Web     -> Flask/Gunicorn，接收页面和 API 请求
```

服务使用同一份 `.env`、SQLite 数据库、`uploads/`、`outputs/` 和 `data/customer_files/`。Web 请求线程不创建后台任务。

## Local Start

先初始化迁移，再启动 Web：

```bash
uv run python -m scripts.init_database
uv run python app.py
```

## Container Start

```bash
docker compose up -d --build bld-matcher
docker compose ps
```

`bld-matcher` 的 healthcheck 调用 `/health/ready`；readiness 使用只读 SQLite 连接，不创建数据库或执行迁移。初始化只由 `scripts.init_database` 完成。不要只看到容器为 running 就判定可用。

## Health And Deployment Probe

```bash
curl -fsS http://127.0.0.1:5055/health/live
curl -fsS http://127.0.0.1:5055/health/ready
uv run python -m scripts.runtime_probe --base-url http://127.0.0.1:5055
```

`/health/ready` 需要以下检查全部成功：

- database：SQLite 可连接；
- migrations：所有迁移已应用；
- business_probe：至少存在一个启用中的管理员，产品表可查询。

`/health/live` 成功但 `/health/ready` 返回 503，表示进程还活着但不应接流量。先看响应内的 `checks`，再检查迁移和管理员状态。

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
BLD_INQUIRY_UPLOAD_RETENTION_DAYS
BLD_INQUIRY_OUTPUT_RETENTION_DAYS
BLD_MATERIAL_UPLOAD_RETENTION_DAYS
BLD_MATERIAL_OUTPUT_RETENTION_DAYS
BLD_CONTRACT_OUTPUT_RETENTION_DAYS
BLD_BACKUP_RETENTION_DAYS
BLD_ARTIFACT_RETENTION_HOURS
BLD_IDEMPOTENCY_RETENTION_HOURS
```

一般上传和输出继续分别由 `BLD_UPLOAD_RETENTION_DAYS`、`BLD_OUTPUT_RETENTION_DAYS` 管理，默认 30 天。`BLD_INQUIRY_*` 仅覆盖询价上传（`inquiry-*`）、询价 Excel（`reYYMMDD-*`）和询价图纸压缩包（`drawings-*`）；`BLD_MATERIAL_*` 覆盖材料上传（`material-plan-*`、`material-data-*`）和生成料单（`*料单.xlsx`、`*料单_2.xlsx` 等重复生成文件）；`BLD_CONTRACT_OUTPUT_RETENTION_DAYS` 覆盖用户输出目录下的采购合同与销售合同。上述专项变量设为 `0` 表示长期保留，NAS 默认使用此值。API v1 artifact 的时效独立由 `BLD_ARTIFACT_RETENTION_HOURS` 管理，仍维持其 Key 所有权隔离。

清理器只处理受控 `uploads/`、`outputs/` 和备份目录。未过期 artifact 引用的输出文件受保护。默认命令始终是 dry-run；不要把 `--apply` 加入未经审查的日常 shell alias。

客户标贴、包装、出库单及 PI/PL/CI 等资料位于 `data/customer_files/`，不属于运行清理器范围并长期保留。数据库记录与文件版本必须成套备份；恢复时不能只恢复 SQLite 或只恢复资料目录。

## API Key Rotation

`BLD_API_KEY_ROTATION_DAYS` 默认 90 天。管理页在达到建议日期后标记“建议轮换”，但不会自动删除 Key。轮换步骤是先创建最小 Scope 的新 Key、更新调用方并验证，再单独删除旧 Key。

## Structured Logs

默认 `LOG_FORMAT=json`，`LOG_LEVEL=INFO`。日志包含时间、级别、logger、message，以及存在时的 request ID、endpoint、method 和稳定错误码。不要把上传内容、密钥或绝对路径放入日志 extra。

## Deployment And Rollback

1. 对运行数据库做 SQLite 一致性备份，并确认运行数据目录不在 Git 操作范围内。
2. 更新代码后先执行 `scripts.init_database`，再启动 Web 服务。
3. 等待 healthcheck 成功，并运行 `scripts.runtime_probe`。
4. 回滚到旧代码必须同时恢复迁移前数据库备份；不要只回滚容器镜像。产品数据同步与 NAS 数据方向仍遵守 `AGENTS.md`，本手册不授权任何运行数据覆盖。
