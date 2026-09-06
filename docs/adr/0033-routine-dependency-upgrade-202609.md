# ADR 0033: Routine Dependency Upgrade Baseline 2026-09

- Status: accepted
- Date: 2026-09-05
- Owners: BLD

## Context

项目核心依赖长期精确钉死在旧版本：Flask 3.0.3、gunicorn 22.0.0、Pillow 11.3.0、pillow-heif 1.1.1、reportlab 4.4.3。长期不升级会持续累积已修复的上游缺陷，且未来被迫升级时跨版本过大、风险集中。本项目有全量 `scripts/verify.py`（契约检查、ruff、路由与 OpenAPI 快照、完整测试套件）作为升级后的验证手段，具备安全升级条件。

## Decision

将 `pyproject.toml` 中钉死的运行时依赖一次性升级到当前最新稳定版本，并重新导出 Docker 构建使用的 `requirements.txt`：

- Flask 3.0.3 → 3.1.3（werkzeug 保持 3.1.8 兼容线）
- gunicorn 22.0.0 → 26.2.0
- Pillow 11.3.0 → 12.3.0
- pillow-heif 1.1.1 → 1.6.0
- reportlab 4.4.3 → 5.0.1
- pydantic 等间接依赖由 `uv lock` 解析到最新兼容版本
- openpyxl、xlrd、xlwt、xlutils 已是最新，保持不变

依赖继续采用精确版本钉死策略，不引入范围约束；以后每隔一到两个月按同样流程做一次例行升级。

## Alternatives

- 只升级补丁版本、冻结大版本：安全性提升有限，大版本债务继续累积，放弃。
- 改用 `>=` 范围约束让 lock 自动浮动：破坏构建可重现性，Docker 镜像与本地环境可能漂移，放弃。

## Consequences

- 正面：获得上游缺陷修复与性能改进；后续升级保持小步幅。
- 风险：Flask 3.1、gunicorn 26、Pillow 12、reportlab 5 均含大版本跨越，可能存在行为变化；由全量 verify 和本机 5055 冒烟验证兜底。
- 回滚：`git revert` 本次提交即可恢复旧锁定版本，无数据迁移。

## Verification

- `uv run python scripts/verify.py` 全部必检项通过。
- 本机 5055 服务重启后 `scripts/runtime_probe.py --base-url http://127.0.0.1:5055` 通过。
