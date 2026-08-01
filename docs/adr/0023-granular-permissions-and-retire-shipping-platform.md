# ADR 0023: 权限细粒度拆分与退役货物识别、发货通知及后台任务平台

- Status: accepted
- Date: 2026-08-01
- Owners: BLD

## Context

权限体系此前按“维护某领域”粗粒度划分：维护客户信息、维护报价记录、合同管理、维护生产资料各是一个权限，无法按岗位区分查看、新增、修正、删除。同时，货物识别与发货通知两个功能在 ADR 0011 完成页面退役后已无网页入口，仅保留权限占位；为其服务的后台任务平台（`app/platform/jobs/`、worker 进程、AI 供应商调用层 `app/platform/ai.py`）以及公开 API `/api/v1/jobs/*` 再无任何生产者，属于需要持续维护却没有使用者的基础设施。

## Decision

1. 权限细粒度拆分：
   - `manage_customers` 拆为查看/新增/修正/删除客户（`view_customers`、`add_customers`、`edit_customers`、`delete_customers`）。
   - `manage_customer_prices` 拆为新增/修正/删除报价（`add_customer_prices`、`edit_customer_prices`、`delete_customer_prices`），并新增“查看历史价格”（`view_price_history`）；产品目录单价可见性统一为“三个报价写权限任一”。
   - `generate_purchase_contract` 拆为查看合同（`view_contracts`）与生成合同（`generate_contract`）。
   - `manage_materials` 拆为维护材料明细（`manage_material_items`，含物料图纸上传）与维护管件资料（`manage_tube_items`）。
   - 迁移 034 按映射为旧权限持有者补齐新权限（含个人覆盖的 allow/deny），再清除旧键及 `recognize_shipments`、`generate_shipping_notice` 的授权行。
2. 账号管理页四视图分工：账号页只管理单个账号的个人权限（下拉选择账号）；账号列表承载列表与账号身份的新增/编辑；角色页只维护角色权限矩阵（下拉选择角色）；角色列表承载角色新增/重命名与删除，角色说明输入框移除。
3. 整体删除货物识别与发货通知：`app/modules/shipping/`、`tools/shipment_photo_recognition.py`、两个权限定义。
4. 一并删除只服务货物识别的后台任务平台：`app/platform/jobs/`、`app/platform/ai.py`、`scripts/run_worker.py`、`scripts/worker_health.py`、docker-compose 的 `bld-worker` 服务、公开 API `/api/v1/jobs/*` 及 `jobs:read`/`jobs:cancel` scope；运行健康检查与运行数据清理同步裁剪 worker/AI 相关部分。
5. 数据库迁移历史（006、017 等）与历史数据、运行数据目录全部保留，不做数据删除。

## Alternatives

- **保留 jobs/AI/worker 平台作为未来任务的基础设施**：平台当前没有任何生产者，保留意味着空转的 worker 容器、健康检查与 API 表面都要继续维护；未来若重新需要后台任务，按当时需求重建比维护空壳更准确，因此不选。
- **目录价可见性继续挂在独立的查看权限**：此前的 `view_product_prices` 实验证明独立查看权限会产生“能报价却看不到目录价”的矛盾配置，统一为写权限任一可以根除该组合，因此不选。
- **拆分迁移不补齐新权限、由管理员手工重配**：上线即破坏现有岗位工作流，因此不选。

## Consequences

- `/api/v1/jobs/*` 三个公开端点与 `jobs:read`、`jobs:cancel` 两个 scope 被移除，属破坏性 API 变更；持有这些 scope 的既有 API Key 继续可用，但相关端点返回 404。
- NAS 部署后 `bld-matcher-worker` 容器随 compose 更新移除；`.env` 中 `SHIPMENT_VISION_*`、`BLD_WORKER_*`、`BLD_JOB_*` 变量成为死配置，需手工清理。
- 旧权限键在角色权限与个人覆盖中的授权行由迁移 034 清理；拥有旧权限的角色自动获得对应新权限，上线后行为与现状一致（查看历史价格按“查看报价记录”持有者补齐）。
- 账号与角色的“身份管理”和“权限管理”在页面上分离，新增账号/角色后需到对应权限页完成授权。
- AI 供应商调用层删除后，项目不再有外部 AI 依赖；`docs/api/ai-contract.md` 随之删除。

## Verification

- 迁移 034 的映射与幂等性由 `tests/test_admin_permissions.py` 用例覆盖。
- 拆分后的门禁（页面列隐藏、接口剔除价格、导出拦截）由 `tests/test_app.py` 端到端用例覆盖。
- 每次提交前运行 `uv run python scripts/verify.py`，确保项目契约和完整测试通过。
