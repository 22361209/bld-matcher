# ADR 0018: Dense List Layout And Data Grid Controls

- Status: accepted
- Date: 2026-07-29
- Owners: BLD

## Context

产品目录、报价记录、材料明细、管件资料和询价结果已共享可调宽表格，但列头筛选、拖拽和静态操作列仍由名为 `product_table` 的页面资产提供。搜索命令区与表格框体也曾被拆成相邻卡片，导致中间露出不同底色或出现不一致的距离。于是一次页面修正无法自然约束其他列表页。

## Decision

1. 共享列头、筛选面板和列拖拽样式迁入 `static/components/data_grid_controls.css`，由 `base.html` 加载；共享交互迁入 `static/components/data_grid_controls.js`，由需要列配置的页面模块导入。
2. 共享 CSS 与模板结构统一使用职责命名：`data-grid-table`、`data-grid-column-*` 和 `data-grid-filter-*`。不再以产品域前缀命名跨页面控件。
3. 密集列表的搜索/命令区与 `data-resizable-grid` 必须放在同一个 `data-section`，并复用 `workspace-command` 产生统一的表面、边框和 12px 过渡间距。
4. 列头文字、筛选触发与静态操作列统一为左对齐、垂直居中；筛选候选沿用 ADR 0017 的既有颜色 token。
5. 项目合同回归测试检查共享组件由基础模板加载、主要列表采用标准语义类、旧 `product_table` 页面资产不再存在。协议的后续变更必须同时修改此 ADR 的后继 ADR、页面协议和测试。

## Consequences

- 产品、报价、材料、管件和询价结果继续保持各自服务端筛选与 URL 行为，但不再拥有独立的列头和候选面板皮肤。
- 新列表页可以复用通用控件，页面 CSS 只保留业务字段和布局差异。
- 本次不修改数据模型、权限、API 或 NAS 部署合同；浏览器需刷新或本地服务重启以获得更新后的静态资源。

## Verification

- `tests/test_project_contract.py`：共享资产、语义类、主要列表复用与旧资产退役。
- `tests/test_admin_add_form_layout.py`：搜索输入与紧邻动作的宽度、缩进和窄屏换行。
- `tests/test_combobox_styles.py`：候选浮层的显式可读色 token。
- `tests/js/data_grid_controls.test.mjs`：列筛选搜索和中文输入法行为。
- `uv run python scripts/verify.py`：项目合同、静态检查、测试和快照总门禁。
