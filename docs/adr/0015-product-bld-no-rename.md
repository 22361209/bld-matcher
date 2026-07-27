# ADR 0015: 产品 BLD NO. 迁移（级联改名）

- Status: accepted
- Date: 2026-07-27
- Owners: BLD

## Context

BLD NO. 是 `products` 表的 UNIQUE 业务主键，也是询价、报价、客户价格记录和人工映射中标识产品的核心字段。产品编辑页面的 BLD NO. 字段长期处于只读状态，因为修改它会破坏跨表一致性。

业务上偶尔需要将已有型号整体替换为新型号（例如 K6009A → K6009C）。不采取行动会导致业务人员被迫停用旧产品并新建产品，历史报价和价格记录仍挂在旧型号下，无法形成统一视图。

## Decision

新增管理员专属的产品型号迁移功能：

1. **权限隔离**：新增 `rename_products` 权限，仅 `admin` 角色默认拥有；Web 入口和 API 均校验该权限。
2. **级联更新**：在单个 SQLite 事务内更新以下表的 `bld_no`：
   - `products`（主记录，同步更新图片/图纸路径字段）
   - `aliases`（人工映射指向的目标）
   - `customer_price_records`（客户价格历史）
   - `quote_records`（报价历史）
3. **媒体文件同步**：重命名 `data/product_images/`、`data/product_images/thumbs/`、`data/drawings/pdf/` 下的相关文件，以及 `data/product_images/archive/`、`data/drawings/archive/` 下的归档目录。
4. **备份与审计**：操作前在 `data/local-backups/` 生成带时间戳的 SQLite 备份；操作后在 `audit_logs` 写入 `"产品型号迁移"` 记录。
5. **缓存失效**：操作完成后调用 `ProductService.invalidate_catalog()`，使产品目录快照重新加载。

## Alternatives

- **只改 products + 保留旧别名**：优点是不改写历史记录；缺点是报表、询价历史、客户价格记录里仍显示旧型号，与"改名"业务语义不符，因此不选。
- **直接运行 SQL 脚本**：可由管理员在数据库层面执行，但违反"AI/自动化不直连运行时数据库"的项目规范，且缺少审计与备份，因此不选。

## Consequences

- 历史数据与新型号保持一致，报表和询价追踪不再断裂。
- 媒体文件重命名与数据库更新跨两个系统，无法做到绝对原子；通过"先移文件、再更新数据库、失败回滚文件"的方式降低风险。
- 操作前自动备份，可在误操作时从 `data/local-backups/` 还原数据库；媒体文件需通过 NAS/Git 备份单独恢复。
- `quote_record_revisions` 中的历史 JSON 快照和 `audit_logs.target_key` 中的旧记录保持不变，以保留当时原始信息。

## Verification

- `tests/test_product_rename.py` 覆盖：管理员成功迁移、editor 被拒绝、目标型号已存在时报错、页面入口可见性。
- 每次提交前运行 `scripts/verify.py`，确保项目契约检查通过。
- 上线后通过审计日志和备份文件监控迁移操作频率。
